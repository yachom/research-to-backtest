"""Research-to-Backtest CLI (README §26).

resolve-company(A1)·collect-financials(A2)는 구현되었다. 나머지 명령의
구현 시점은 docs/MILESTONES.md의 Phase 표를 따른다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from research_backtest import __version__
from research_backtest.core.config import get_settings, load_dart_config
from research_backtest.core.constants import FsDiv, PeriodicReportType, ReprtCode, StatementType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import (
    CorpCodeRegistry,
    corp_code_cache_dir,
    load_corp_code_registry,
)
from research_backtest.core.dart.disclosure_search import find_periodic_filings, latest_filing
from research_backtest.core.dart.financial_api import (
    JSONL_FILENAME,
    MIN_SUPPORTED_YEAR,
    CollectionSummary,
    financials_out_dir,
)
from research_backtest.core.dart.financial_api import (
    collect_financials as run_collect_financials,
)
from research_backtest.core.dart.models import DartFiling, ResolveMethod, ResolveResult
from research_backtest.core.exceptions import ConfigError, DartApiError, DartTransportError
from research_backtest.core.models import DartCorporation

app = typer.Typer(
    name="r2b",
    help="AI 기반 기업 리서치 및 투자전략 검증 시스템 (OpenDART · XBRL · 백테스트)",
    no_args_is_help=True,
    # 예외 트레이스의 로컬 변수 출력에 인증키가 노출되지 않도록 비활성화 (README §30.2)
    pretty_exceptions_show_locals=False,
)
console = Console()

KST = ZoneInfo("Asia/Seoul")

RESOLVE_FAILURE_EXIT_CODE = 1
NOT_IMPLEMENTED_EXIT_CODE = 2
CONFIG_ERROR_EXIT_CODE = 3


def _not_implemented(milestone: str) -> None:
    console.print(
        f"[yellow]아직 구현되지 않은 명령입니다 — {milestone}에서 구현 예정"
        f" (docs/MILESTONES.md 참고).[/yellow]"
    )
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


@app.command()
def version() -> None:
    """버전을 출력한다."""
    console.print(__version__)


@app.command("resolve-company")
def resolve_company(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    as_of_date: Annotated[
        str | None,
        typer.Option("--as-of-date", help="분석 기준일 YYYY-MM-DD (기본: 오늘 KST)"),
    ] = None,
    refresh_corp_codes: Annotated[
        bool,
        typer.Option("--refresh-corp-codes", help="고유번호 파일 캐시를 강제로 갱신"),
    ] = False,
) -> None:
    """기업명·종목코드로 DART 법인(corp_code)을 식별하고 최근 정기보고서를 찾는다.

    README §19.1~19.2(P1-01/02), §31 Milestone 1 완료 조건에 해당한다.
    종료 코드: 0 성공 / 1 NOT_FOUND·AMBIGUOUS·DART 오류 / 3 설정 오류(키 미설정 등).
    """
    try:
        settings = get_settings()
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    try:
        as_of = date.fromisoformat(as_of_date) if as_of_date else datetime.now(KST).date()
    except ValueError as err:
        raise typer.BadParameter("--as-of-date는 YYYY-MM-DD 형식이어야 합니다.") from err

    try:
        with DartClient(
            api_key,
            timeout=dart_config.timeout_seconds,
            max_attempts=dart_config.retry.max_attempts,
            backoff_seconds=dart_config.retry.backoff_seconds,
        ) as client:
            registry = load_corp_code_registry(
                client,
                corp_code_cache_dir(settings.data_dir),
                refresh_days=dart_config.corp_code_cache.refresh_days,
                force=refresh_corp_codes,
            )
            corp, method = _resolve_or_exit(registry, company)
            filings = find_periodic_filings(
                client, corp.corp_code, as_of_date=as_of, lookback_years=2
            )
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err

    _print_company(corp, method)
    annual = latest_filing(filings, PeriodicReportType.ANNUAL)
    interim = _latest_interim(filings)
    _print_filings(annual, interim)


def _resolve_or_exit(
    registry: CorpCodeRegistry, company: str
) -> tuple[DartCorporation, ResolveMethod]:
    """기업 식별 성공 시 (기업, 매칭 방법)을 반환하고, 실패 시 후보 출력 후 exit 1.

    resolve-company·collect-financials가 공유하는 규칙이다(명세 A2 §0, §5) —
    AMBIGUOUS는 후보 테이블, NOT_FOUND는 안내 메시지를 출력한다.
    """
    result = registry.resolve(company)
    if result.matched is None:
        _print_resolve_failure(company, result)
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE)
    return result.matched, result.method


def _latest_interim(filings: Sequence[DartFiling]) -> DartFiling | None:
    """분기·반기보고서 중 최신 1건 (README §31 M1 완료 조건의 두 번째 행)."""
    interim_types = (PeriodicReportType.HALF, PeriodicReportType.Q1, PeriodicReportType.Q3)
    interim = [f for f in filings if f.report_type in interim_types]
    return max(interim, key=lambda f: (f.rcept_dt, f.rcept_no), default=None)


def _print_company(corp: DartCorporation, method: str) -> None:
    table = Table(title="기업 식별 결과 (README §19.1)")
    table.add_column("항목")
    table.add_column("값")
    table.add_row("corp_code", corp.corp_code)
    table.add_row("corp_name", corp.corp_name)
    table.add_row("stock_code", corp.stock_code or "-")
    table.add_row("상장 여부", "상장" if corp.stock_code else "비상장")
    table.add_row("매칭 방법", method)
    console.print(table)


def _print_filings(annual: DartFiling | None, interim: DartFiling | None) -> None:
    table = Table(title="최근 정기보고서 (README §19.2)")
    for column in ("구분", "rcept_no", "rcept_dt", "report_nm"):
        table.add_column(column)
    for label, filing in (("최근 사업보고서", annual), ("최근 분기·반기보고서", interim)):
        if filing is None:
            table.add_row(label, "-", "-", "없음")
        else:
            table.add_row(label, filing.rcept_no, filing.rcept_dt.isoformat(), filing.report_nm)
    console.print(table)


def _print_resolve_failure(query: str, result: ResolveResult) -> None:
    if result.method == "AMBIGUOUS":
        console.print(f"[yellow]'{query}'에 대한 후보가 여러 개입니다 (AMBIGUOUS).[/yellow]")
        table = Table(title="후보 기업 (상장 우선, 최대 10)")
        for column in ("corp_code", "stock_code", "corp_name"):
            table.add_column(column)
        for corp in result.candidates:
            table.add_row(corp.corp_code, corp.stock_code or "-", corp.corp_name)
        console.print(table)
    else:
        console.print(f"[red]'{query}'에 해당하는 기업을 찾지 못했습니다 (NOT_FOUND).[/red]")
    console.print("6자리 종목코드로 다시 시도하면 정확히 식별됩니다 (예: --company 000660).")


@app.command("collect-financials")
def collect_financials(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    from_year: Annotated[
        int, typer.Option("--from-year", help=f"수집 시작 사업연도 ({MIN_SUPPORTED_YEAR} 이상)")
    ],
    to_year: Annotated[int, typer.Option("--to-year", help="수집 종료 사업연도")],
    scopes: Annotated[
        list[str] | None,
        typer.Option("--scopes", help="재무제표 구분 (CFS/OFS, 반복 지정 가능 — 기본 둘 다)"),
    ] = None,
    force_download: Annotated[
        bool, typer.Option("--force-download", help="캐시를 무시하고 재수집 (README §8.3)")
    ] = False,
    include_xbrl: Annotated[
        bool, typer.Option("--include-xbrl", help="XBRL 원본 ZIP 함께 수집 (Milestone B1)")
    ] = False,
) -> None:
    """DART 전체 재무제표 API로 연도별 CFS·OFS raw를 수집한다 (README §19.3, §31 M2).

    캐시된 요청은 재호출하지 않으며(멱등), 미제출 보고서(013)는 NO_DATA로
    기록된다 — 실패가 아니다. 종료 코드: 0 성공 / 1 식별 실패·DART 오류 /
    3 설정 오류.
    """
    if from_year > to_year:
        raise typer.BadParameter(
            f"--from-year({from_year})는 --to-year({to_year})보다 클 수 없습니다."
        )
    if from_year < MIN_SUPPORTED_YEAR:
        raise typer.BadParameter(
            f"전체 재무제표 API는 {MIN_SUPPORTED_YEAR}년 이후 사업연도만 제공합니다 (README §6.4)."
        )
    fs_divs = _parse_scopes(scopes)
    if include_xbrl:
        console.print(
            "[yellow]XBRL 수집은 Milestone B1에서 구현됩니다 — 이번 실행에서는 무시[/yellow]"
        )

    try:
        settings = get_settings()
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    try:
        with DartClient(
            api_key,
            timeout=dart_config.timeout_seconds,
            max_attempts=dart_config.retry.max_attempts,
            backoff_seconds=dart_config.retry.backoff_seconds,
        ) as client:
            registry = load_corp_code_registry(
                client,
                corp_code_cache_dir(settings.data_dir),
                refresh_days=dart_config.corp_code_cache.refresh_days,
            )
            corp, _method = _resolve_or_exit(registry, company)
            out_dir = financials_out_dir(settings.data_dir, corp.corp_code)
            summary = run_collect_financials(
                client,
                corp.corp_code,
                from_year=from_year,
                to_year=to_year,
                fs_divs=fs_divs,
                out_dir=out_dir,
                force=force_download,
                min_interval_seconds=dart_config.min_interval_seconds,
            )
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err

    _print_collection(corp, summary, out_dir)


_REPRT_LABELS: dict[ReprtCode, str] = {
    ReprtCode.Q1: "1분기보고서",
    ReprtCode.HALF: "반기보고서",
    ReprtCode.Q3: "3분기보고서",
    ReprtCode.ANNUAL: "사업보고서",
}


def _parse_scopes(scopes: list[str] | None) -> tuple[FsDiv, ...]:
    """--scopes 값을 검증·중복 제거한다 — CFS/OFS 외 값은 BadParameter (명세 A2 §5)."""
    if not scopes:
        return (FsDiv.CFS, FsDiv.OFS)
    parsed: dict[FsDiv, None] = {}
    for raw in scopes:
        try:
            parsed[FsDiv(raw.strip().upper())] = None
        except ValueError as err:
            raise typer.BadParameter(f"--scopes는 CFS/OFS만 허용합니다: {raw!r}") from err
    return tuple(parsed)


def _format_sj_div_counts(counts: dict[str, int]) -> str:
    """sj_div별 행수 요약 — BS·IS·CIS·CF·SCE 순서, 없는 종류는 생략."""
    if not counts:
        return "-"
    known = [statement.value for statement in StatementType]
    ordered = [key for key in known if key in counts] + [k for k in counts if k not in known]
    return " ".join(f"{key}:{counts[key]}" for key in ordered)


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as fp:
        return sum(1 for line in fp if line.strip())


def _print_collection(corp: DartCorporation, summary: CollectionSummary, out_dir: Path) -> None:
    """수집 결과 테이블 + 저장 경로·jsonl 라인 수 출력 (명세 A2 §5)."""
    table = Table(title=f"전체 재무제표 수집 결과 — {corp.corp_name} ({summary.corp_code})")
    for column in ("연도", "보고서", "scope", "결과", "행수", "sj_div별 행수"):
        table.add_column(column)
    for outcome in summary.outcomes:
        table.add_row(
            outcome.bsns_year,
            f"{_REPRT_LABELS[outcome.reprt_code]}({outcome.reprt_code.value})",
            outcome.fs_div.value,
            outcome.result,
            str(outcome.row_count),
            _format_sj_div_counts(outcome.sj_div_counts),
        )
    console.print(table)
    line_count = _count_jsonl_lines(out_dir / JSONL_FILENAME)
    console.print(f"저장 경로: {out_dir}")
    console.print(f"병합본 {JSONL_FILENAME}: {line_count}라인")


@app.command("parse-xbrl")
def parse_xbrl(
    corp_code: Annotated[str, typer.Option("--corp-code", help="DART 8자리 법인코드")],
    rcept_no: Annotated[str, typer.Option("--rcept-no", help="공시 접수번호")],
) -> None:
    """XBRL 원본에서 Fact·Context·Unit·Dimension을 추출한다."""
    _not_implemented("Milestone B2")


@app.command("reconcile-financials")
def reconcile_financials(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    year: Annotated[int, typer.Option("--year", help="사업연도")],
    report: Annotated[str, typer.Option("--report", help="보고서 종류 (annual/half/q1/q3)")],
) -> None:
    """전체 재무제표 API와 XBRL 원본의 대표 계정 수치를 교차검증한다."""
    _not_implemented("Milestone B3")


@app.command()
def research(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    as_of_date: Annotated[str, typer.Option("--as-of-date", help="분석 기준일 (YYYY-MM-DD)")],
    lookback_years: Annotated[int, typer.Option("--lookback-years", help="분석 대상 연수")] = 5,
) -> None:
    """기업분석 보고서와 투자 가설을 생성한다."""
    _not_implemented("Milestone C1")


@app.command()
def backtest(
    hypothesis: Annotated[str, typer.Option("--hypothesis", help="투자 가설 JSON 경로")],
    start_date: Annotated[str, typer.Option("--start-date", help="백테스트 시작일")],
    end_date: Annotated[str, typer.Option("--end-date", help="백테스트 종료일")],
    benchmark: Annotated[str, typer.Option("--benchmark", help="벤치마크 지수")] = "KOSPI",
) -> None:
    """전략을 과거 데이터로 검증한다."""
    _not_implemented("Milestone A6")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
