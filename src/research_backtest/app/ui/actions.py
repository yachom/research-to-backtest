"""쓰기·상태 전이 액션 (docs/specs/W3c-report-ui.md §3, S1 소유).

이 모듈은 ``app/commands/hitl_flow.py``·``app/commands/backtest_cmd.py``의
typer 명령 함수를 **호출하지 않는다**(§3.1 — typer.Exit를 던지는 CLI 함수는
호출 금지). 대신 그 함수들이 조립하는 core/research/quant API를 **동일한
순서**로 직접 호출한다. 각 함수의 docstring에 대응하는 CLI 명령과 절 번호를
남겨 조립 순서의 근거를 추적할 수 있게 한다(비즈니스 로직 재구현 금지 —
검증·게이트·상태 전이 규칙 자체는 core.hitl가 소유하고, 여기서는 CLI가 쓰는
작은 오케스트레이션 헬퍼(허용 상태 판정 등)만 CLI와 동일하게 다시 선언한다).

실패는 그대로 전파한다 — ``ApprovalGateError``·``DataValidationError``·
``StrategyValidationError``·``ConfigError``·``pydantic.ValidationError``.
CLI는 이를 잡아 종료 코드로 바꾸지만(§3), 화면은 ``st.error``로 바꾼다
(``app/ui/screens.py``가 호출부에서 처리한다) — 예외 타입과 메시지 자체는
CLI와 동일하다(게이트 약화 금지).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from research_backtest.core.config import Settings, load_dart_config
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import corp_code_cache_dir, load_corp_code_registry
from research_backtest.core.dart.models import ResolveResult
from research_backtest.core.exceptions import (
    ApprovalGateError,
    DataValidationError,
    StrategyValidationError,
)
from research_backtest.core.financials.pipeline import METRICS_FILENAME, financials_out_dir
from research_backtest.core.hitl.diff import diff_strategies
from research_backtest.core.hitl.gates import (
    ensure_hypothesis_approved,
    ensure_state_at_least,
    ensure_strategy_approved,
)
from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    HumanInvestmentHypothesis,
    HypothesisCandidate,
    HypothesisStatus,
    RunManifest,
    StrategyReview,
    now_kst_iso,
)
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    RunState,
    advance,
    create_run_state,
    generate_run_id,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.hitl.validation import (
    FileEvidenceStore,
    approve_hypothesis,
    validate_analyst_view,
    validate_hypothesis,
)
from research_backtest.core.llm import LlmCallMetadata, create_llm_client, load_llm_config
from research_backtest.core.market.collector import (
    DAILY_FILENAME,
    market_calendar_path,
    market_normalized_stock_dir,
)
from research_backtest.core.models import DartCorporation
from research_backtest.quant.backtest.costs import BacktestConfig, load_backtest_config
from research_backtest.quant.backtest.metrics import BacktestResult
from research_backtest.quant.backtest.runner import (
    BACKTEST_RESULT_FILENAME,
    DAILY_PORTFOLIO_FILENAME,
    TRADE_LOG_FILENAME,
    execute_approved_strategy,
)
from research_backtest.quant.strategy.compiler import compile_strategy
from research_backtest.quant.strategy.draft import DEFAULT_PROMPTS_DIR, draft_strategy
from research_backtest.quant.strategy.registry import resolve_indicator
from research_backtest.quant.strategy.schema import parse_strategy_spec
from research_backtest.research.candidates.generator import (
    CANDIDATE_ANALYSIS_PROMPT_NAME,
    HYPOTHESIS_CANDIDATE_PROMPT_NAME,
    PROMPTS_DIR,
    generate_candidate_analysis,
    generate_hypothesis_candidates,
)
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    build_financial_evidence,
)

KST = ZoneInfo("Asia/Seoul")

#: 백테스트 시작일 기본값 — app/commands/backtest_cmd.py DEFAULT_START_DATE와 동일(§4.4).
DEFAULT_BACKTEST_START_DATE = date(2016, 1, 1)

#: 가설 판정(1804 §10) → HumanInvestmentHypothesis.status 매핑
#: (hitl_flow.py `_DECISION_TO_STATUS`, §5.6과 동일).
_DECISION_TO_STATUS: dict[str, HypothesisStatus] = {
    "SUPPORTED": HypothesisStatus.SUPPORTED,
    "PARTIALLY_SUPPORTED": HypothesisStatus.PARTIALLY_SUPPORTED,
    "REJECTED": HypothesisStatus.REJECTED,
    "REVISED": HypothesisStatus.REVISED,
    "INCONCLUSIVE": HypothesisStatus.TESTED,
}

#: BacktestInterpretation.hypothesis_decision 허용값(1804 §10, core.hitl.models 문서화).
HYPOTHESIS_DECISION_OPTIONS: tuple[str, ...] = (
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "REJECTED",
    "REVISED",
    "INCONCLUSIVE",
)


# ---------------------------------------------------------------------------
# 공통 헬퍼 — hitl_flow.py의 동명 private 헬퍼와 동일 로직(§3, 재구현이 아니라
# 동일 규칙의 재선언 — 게이트 약화 금지).
# ---------------------------------------------------------------------------


def _check_allowed_state(run_state: RunState, allowed: set[PipelineState], *, command: str) -> None:
    """hitl_flow.py `_check_allowed_state`와 동일한 허용 상태 판정(§6.3)."""
    if run_state.current_state in allowed:
        return
    current_idx = FORWARD_ORDER.index(run_state.current_state)
    min_idx = min(FORWARD_ORDER.index(state) for state in allowed)
    hint = (
        "이전 단계를 먼저 완료하세요."
        if current_idx < min_idx
        else "이 단계로의 회귀는 허용되지 않습니다."
    )
    raise ApprovalGateError(
        f"'{command}'는 현재 상태({run_state.current_state.value})에서 실행할 수 없습니다. {hint}"
    )


def _supported_variables(selected: list[str]) -> set[str]:
    """A5 Indicator Registry 지원 변수만 골라낸다(hitl_flow.py `_supported_variables`, §5.4)."""
    supported: set[str] = set()
    for name in selected:
        try:
            resolve_indicator(name)
        except StrategyValidationError:
            continue
        supported.add(name)
    return supported


def _git_short_hash() -> str | None:
    """현재 커밋 짧은 해시 — best-effort(hitl_flow.py `_git_short_hash`와 동일)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def load_strategy_name(store: RunStore) -> str | None:
    """strategy_spec.json의 strategy_name(hitl_flow.py `_load_strategy_name`, §5.6)."""
    path = store.run_dir / "strategy_spec.json"
    if not path.exists():
        raise DataValidationError(f"strategy_spec.json이 없습니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise DataValidationError(f"strategy_spec.json이 올바른 JSON이 아닙니다: {path}") from err
    name = data.get("strategy_name") if isinstance(data, dict) else None
    return str(name) if name is not None else None


def try_load_candidate_analysis(store: RunStore) -> CandidateAnalysis | None:
    """candidate_analysis.json이 없으면(아직 미생성) None — 미존재는 정상 상태다."""
    try:
        return store.load_candidate_analysis()
    except DataValidationError:
        return None


def try_load_hypothesis_candidates(store: RunStore) -> list[HypothesisCandidate] | None:
    try:
        return store.load_hypothesis_candidates()
    except DataValidationError:
        return None


def try_load_analyst_view(store: RunStore) -> AnalystView | None:
    try:
        return store.load_analyst_view()
    except DataValidationError:
        return None


def try_load_human_hypothesis(store: RunStore) -> HumanInvestmentHypothesis | None:
    try:
        return store.load_human_hypothesis()
    except DataValidationError:
        return None


def try_load_strategy_draft(store: RunStore) -> dict[str, object] | None:
    try:
        return store.load_strategy_draft()
    except DataValidationError:
        return None


def try_load_strategy_review(store: RunStore) -> StrategyReview | None:
    try:
        return store.load_strategy_review()
    except DataValidationError:
        return None


def try_load_backtest_interpretation(store: RunStore) -> BacktestInterpretation | None:
    try:
        return store.load_backtest_interpretation()
    except DataValidationError:
        return None


def load_evidence_manifest_ids(store: RunStore) -> list[str]:
    """evidence_manifest.json의 evidence_id 목록(화면③ 후보 표시용, 없으면 빈 리스트)."""
    path = store.run_dir / "evidence_manifest.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [str(item["evidence_id"]) for item in raw["evidence"]]
    except (KeyError, TypeError, json.JSONDecodeError):
        return []


def load_backtest_result(store: RunStore) -> BacktestResult | None:
    path = store.run_dir / BACKTEST_RESULT_FILENAME
    if not path.exists():
        return None
    return BacktestResult.model_validate_json(path.read_text(encoding="utf-8"))


def load_daily_portfolio(store: RunStore) -> pd.DataFrame | None:
    path = store.run_dir / DAILY_PORTFOLIO_FILENAME
    if not path.exists():
        return None
    return pd.read_csv(path, parse_dates=["date"])


def load_trade_log(store: RunStore) -> pd.DataFrame | None:
    path = store.run_dir / TRADE_LOG_FILENAME
    if not path.exists():
        return None
    return pd.read_csv(path)


def load_robustness_report(store: RunStore) -> dict[str, Any] | None:
    """robustness_report.json이 있으면 원시 dict로 반환한다(R1 산출물, 있으면만 표시).

    R1의 ``RobustnessReport`` 모델(quant/backtest/robustness.py)에 의존하지
    않는다 — S1·R1은 병합 순서가 무관하므로(명세 §1) 이 파일이 아직 없을 수도
    있고, 있어도 스키마 임포트 없이 관대하게(dict) 읽어 존재하는 필드만
    표로 보여준다.
    """
    path = store.run_dir / "robustness_report.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# 화면① — create-run (hitl_flow.py `_create_run_impl`, §5.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolveFailure:
    """기업 식별 실패 — AMBIGUOUS(candidates 있음) 또는 NOT_FOUND."""

    query: str
    result: ResolveResult


def resolve_corp(company: str, settings: Settings) -> DartCorporation | ResolveFailure:
    """기업명·종목코드로 DartCorporation을 식별한다(hitl_flow.py `_resolve_corp`, §5.1).

    조립 순서: ``require_dart_api_key`` → ``load_dart_config`` → ``DartClient``
    컨텍스트 안에서 ``load_corp_code_registry`` → ``registry.resolve``.
    """
    api_key = settings.require_dart_api_key()
    dart_config = load_dart_config()
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
        result = registry.resolve(company)
    if result.matched is None:
        return ResolveFailure(query=company, result=result)
    return result.matched


def ensure_data_ready(corp: DartCorporation, stock_code: str, settings: Settings) -> list[str]:
    """create-run의 데이터 준비 검사 재현(hitl_flow.py `_ensure_data_ready`, §5.1).

    수집을 트리거하지 않고 존재만 확인한다. 반환값이 빈 리스트면 준비 완료,
    아니면 누락 메시지 목록(화면이 st.error로 나열).
    """
    missing: list[str] = []
    metrics_path = financials_out_dir(settings.data_dir, corp.corp_code) / METRICS_FILENAME
    if not metrics_path.exists():
        missing.append(f"재무 지표({metrics_path}) — r2b build-financials 먼저 실행하세요.")
    daily_path = market_normalized_stock_dir(settings.data_dir, stock_code) / DAILY_FILENAME
    if not daily_path.exists():
        missing.append(f"시장 데이터({daily_path}) — r2b collect-market 먼저 실행하세요.")
    calendar_path = market_calendar_path(settings.data_dir)
    if not calendar_path.exists():
        missing.append(f"거래일 캘린더({calendar_path}) — r2b collect-market 먼저 실행하세요.")
    return missing


@dataclass(frozen=True)
class CreateRunResult:
    run_id: str
    run_state: RunState


def create_run(
    settings: Settings, *, company: str, as_of: date
) -> CreateRunResult | ResolveFailure:
    """create-run 동작 재현(hitl_flow.py `_create_run_impl`, §5.1).

    조립 순서: 기업 식별(:func:`resolve_corp`) → 상장 확인 → 데이터 준비 검사
    (:func:`ensure_data_ready`) → run_id 발급(``generate_run_id``) →
    RunManifest·RunState 생성·저장. 실패는 :class:`ResolveFailure`(식별 실패,
    화면이 후보 표를 그릴 수 있게 구조화된 값으로 반환) 또는
    :class:`DataValidationError`(비상장·데이터 미비, 문자열 메시지로 충분)다.
    """
    resolved = resolve_corp(company, settings)
    if isinstance(resolved, ResolveFailure):
        return resolved
    corp = resolved

    if corp.stock_code is None:
        raise DataValidationError(
            f"'{corp.corp_name}'은(는) 비상장 법인입니다 — run을 생성할 수 없습니다."
        )

    missing = ensure_data_ready(corp, corp.stock_code, settings)
    if missing:
        raise DataValidationError(
            "데이터 준비가 완료되지 않았습니다:\n" + "\n".join(f"- {item}" for item in missing)
        )

    now = datetime.now(KST)
    run_id = generate_run_id(corp.corp_eng_name or corp.corp_name, now)
    manifest = RunManifest(
        run_id=run_id,
        company_query=company,
        corp_code=corp.corp_code,
        corp_name=corp.corp_name,
        corp_eng_name=corp.corp_eng_name,
        stock_code=corp.stock_code,
        as_of_date=as_of.isoformat(),
        created_at=now_kst_iso(),
        code_version=_git_short_hash(),
    )
    store = RunStore(settings.outputs_dir, run_id)
    store.save_run_manifest(manifest)
    run_state = create_run_state(run_id, corp.corp_name, as_of.isoformat(), actor="user")
    store.save_run_state(run_state)
    return CreateRunResult(run_id=run_id, run_state=run_state)


# ---------------------------------------------------------------------------
# 화면② — generate-candidates (hitl_flow.py `run_generate_candidates`, §2.2·§2.3)
# ---------------------------------------------------------------------------


def _usage_record(
    stage: str,
    *,
    metadata: LlmCallMetadata,
    prompt_name: str,
    ai_role: str,
    input_ids: list[str],
    output_ids: list[str],
) -> AIUsageRecord:
    """AIUsageRecord 1건 생성(hitl_flow.py `_usage_record`와 동일)."""
    now = datetime.now(KST)
    return AIUsageRecord(
        usage_id=f"usage-{stage}-{now:%Y%m%d%H%M%S}",
        stage=stage,
        model=metadata.model,
        prompt_name=prompt_name,
        prompt_version="v1",
        input_artifact_ids=input_ids,
        output_artifact_ids=output_ids,
        ai_role=ai_role,
        human_review_required=True,
        human_changes_summary=None,
        created_at=now_kst_iso(),
    )


def ensure_candidates_stage(run_state: RunState) -> None:
    """generate-candidates 재생성 게이트(hitl_flow.py `_ensure_candidates_stage`, §2.2)."""
    if FORWARD_ORDER.index(run_state.current_state) >= FORWARD_ORDER.index(
        PipelineState.ANALYST_VIEW_APPROVED
    ):
        raise ApprovalGateError(
            f"이미 분석 관점 이후 단계({run_state.current_state.value})로 진행된 실행이라 "
            "후보를 재생성할 수 없습니다 — 새 run을 만들어 다시 시작하세요."
        )


@dataclass(frozen=True)
class CandidateGenerationResult:
    package: EvidencePackage
    analysis: CandidateAnalysis
    candidates: list[HypothesisCandidate]
    analysis_meta: LlmCallMetadata
    candidates_meta: LlmCallMetadata
    regenerated: bool


def generate_candidates(
    settings: Settings, store: RunStore, run_state: RunState, *, lookback_years: int = 5
) -> tuple[CandidateGenerationResult, RunState]:
    """generate-candidates 재현(hitl_flow.py `run_generate_candidates`, §2.2·§2.3).

    조립 순서: run_manifest 로드 → 재생성 게이트 → ``build_financial_evidence``
    → ``EvidencePackageStore.save`` → LLM 클라이언트 생성 →
    ``generate_candidate_analysis`` → ``generate_hypothesis_candidates`` →
    저장(candidate_analysis·hypothesis_candidates) → AIUsageRecord 2건 append
    → (최초 생성이면) 상태 2단계 전진(``CANDIDATE_ANALYSIS_READY`` →
    ``AWAITING_ANALYST_VIEW``).
    """
    manifest = store.load_run_manifest()
    ensure_candidates_stage(run_state)
    regenerate = run_state.current_state in {
        PipelineState.CANDIDATE_ANALYSIS_READY,
        PipelineState.AWAITING_ANALYST_VIEW,
    }
    as_of = date.fromisoformat(manifest.as_of_date)

    package = build_financial_evidence(
        manifest.corp_code, as_of=as_of, data_dir=settings.data_dir, lookback_years=lookback_years
    )
    EvidencePackageStore(store.run_dir).save(package)
    config = load_llm_config()
    client = create_llm_client(config, settings)
    analysis, analysis_meta = generate_candidate_analysis(
        package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=config.max_attempts
    )
    candidates, candidates_meta = generate_hypothesis_candidates(
        package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=config.max_attempts
    )

    store.save_candidate_analysis(analysis)
    store.save_hypothesis_candidates(candidates)
    store.append_ai_usage(
        _usage_record(
            "candidate_analysis",
            metadata=analysis_meta,
            prompt_name=CANDIDATE_ANALYSIS_PROMPT_NAME,
            ai_role="후보 정리",
            input_ids=["evidence_package.json"],
            output_ids=["candidate_analysis.json"],
        )
    )
    store.append_ai_usage(
        _usage_record(
            "hypothesis_candidate",
            metadata=candidates_meta,
            prompt_name=HYPOTHESIS_CANDIDATE_PROMPT_NAME,
            ai_role="가설 후보 제시",
            input_ids=["candidate_analysis.json", "evidence_package.json"],
            output_ids=["hypothesis_candidates.json"],
        )
    )

    if not regenerate:
        note = f"generate-candidates: model={analysis_meta.model}, 가설 후보 {len(candidates)}건"
        run_state = advance(
            run_state, PipelineState.CANDIDATE_ANALYSIS_READY, actor="system", note=note
        )
        run_state = advance(
            run_state, PipelineState.AWAITING_ANALYST_VIEW, actor="system", note="후보 검토 대기"
        )
        store.save_run_state(run_state)

    result = CandidateGenerationResult(
        package=package,
        analysis=analysis,
        candidates=candidates,
        analysis_meta=analysis_meta,
        candidates_meta=candidates_meta,
        regenerated=regenerate,
    )
    return result, run_state


# ---------------------------------------------------------------------------
# 화면③ — create-analyst-view (hitl_flow.py, §5.3)
# ---------------------------------------------------------------------------


def _advance_analyst_view(run_state: RunState, *, actor: str) -> RunState:
    """hitl_flow.py `_advance_analyst_view`와 동일한 회귀 후 재전진 규칙(§5.3)."""
    if run_state.current_state == PipelineState.ANALYST_VIEW_APPROVED:
        run_state = advance(
            run_state, PipelineState.AWAITING_ANALYST_VIEW, actor=actor, note="관점 재작성"
        )
    return advance(run_state, PipelineState.ANALYST_VIEW_APPROVED, actor=actor)


def save_analyst_view(store: RunStore, run_state: RunState, view: AnalystView) -> RunState:
    """create-analyst-view 재현(hitl_flow.py, §5.3).

    조립 순서: 허용 상태 검사 → evidence 실존 검증(``validate_analyst_view``)
    → 저장 → 상태 전진.
    """
    _check_allowed_state(
        run_state,
        {PipelineState.AWAITING_ANALYST_VIEW, PipelineState.ANALYST_VIEW_APPROVED},
        command="화면③ 분석 관점 저장",
    )
    evidence_store = FileEvidenceStore.from_manifest(store.run_dir / "evidence_manifest.json")
    validate_analyst_view(view, evidence_store)
    store.save_analyst_view(view)
    run_state = _advance_analyst_view(run_state, actor="user")
    store.save_run_state(run_state)
    return run_state


# ---------------------------------------------------------------------------
# 화면④ — create-hypothesis (hitl_flow.py, §5.4)
# ---------------------------------------------------------------------------


def _advance_hypothesis(run_state: RunState, target: PipelineState, *, actor: str) -> RunState:
    """hitl_flow.py `_advance_hypothesis`와 동일한 전이 규칙(§5.4)."""
    current = run_state.current_state
    if current == target:
        return run_state
    if target == PipelineState.HYPOTHESIS_DRAFT:
        note = "가설 수정(회귀)" if current == PipelineState.HYPOTHESIS_APPROVED else None
        return advance(run_state, PipelineState.HYPOTHESIS_DRAFT, actor=actor, note=note)
    if current == PipelineState.ANALYST_VIEW_APPROVED:
        run_state = advance(run_state, PipelineState.HYPOTHESIS_DRAFT, actor=actor)
    return advance(run_state, PipelineState.HYPOTHESIS_APPROVED, actor=actor)


def save_hypothesis(
    store: RunStore, run_state: RunState, hypothesis: HumanInvestmentHypothesis
) -> RunState:
    """create-hypothesis 재현(hitl_flow.py, §5.4).

    조립 순서: 허용 상태 검사 → view_id 정합 검사 → 지원 변수 판정
    (``resolve_indicator``) → ``validate_hypothesis`` → status는 DRAFT/APPROVED만
    허용 → 저장 → 상태 전진.
    """
    _check_allowed_state(
        run_state,
        {
            PipelineState.ANALYST_VIEW_APPROVED,
            PipelineState.HYPOTHESIS_DRAFT,
            PipelineState.HYPOTHESIS_APPROVED,
        },
        command="화면④ 투자 가설 저장",
    )
    analyst_view = store.load_analyst_view()
    if hypothesis.view_id != analyst_view.view_id:
        raise DataValidationError(
            f"가설의 view_id({hypothesis.view_id})가 저장된 analyst_view.view_id"
            f"({analyst_view.view_id})와 일치하지 않습니다."
        )

    supported = _supported_variables(hypothesis.selected_variables)
    evidence_store = FileEvidenceStore.from_manifest(store.run_dir / "evidence_manifest.json")
    validate_hypothesis(hypothesis, evidence_store, supported)

    if hypothesis.status not in (HypothesisStatus.DRAFT, HypothesisStatus.APPROVED):
        raise DataValidationError(
            f"작성 단계에서는 DRAFT/APPROVED만 허용합니다: {hypothesis.status.value}"
        )

    store.save_human_hypothesis(hypothesis)
    target = (
        PipelineState.HYPOTHESIS_DRAFT
        if hypothesis.status == HypothesisStatus.DRAFT
        else PipelineState.HYPOTHESIS_APPROVED
    )
    run_state = _advance_hypothesis(run_state, target, actor="user")
    store.save_run_state(run_state)
    return run_state


def approve_hypothesis_draft(
    hypothesis: HumanInvestmentHypothesis, *, approved_by: str
) -> HumanInvestmentHypothesis:
    """가설 승인 필드를 채운다 — core.hitl.validation.approve_hypothesis 그대로 재사용."""
    return approve_hypothesis(hypothesis, approved_by=approved_by)


# ---------------------------------------------------------------------------
# 화면⑤ — generate-strategy-draft · approve-strategy (hitl_flow.py, §5.5)
# ---------------------------------------------------------------------------


def generate_strategy_draft_action(
    settings: Settings, store: RunStore, run_state: RunState
) -> tuple[dict[str, object], LlmCallMetadata, RunState]:
    """generate-strategy-draft 재현(hitl_flow.py, §5.5).

    조립 순서: ``ensure_state_at_least(HYPOTHESIS_APPROVED)`` →
    ``ensure_hypothesis_approved`` → 허용 상태 검사 → run_manifest 로드 → LLM
    클라이언트 생성 → ``draft_strategy`` → 저장 → AIUsageRecord append →
    (최초 생성이면) 상태 2단계 전진.
    """
    ensure_state_at_least(run_state, PipelineState.HYPOTHESIS_APPROVED)
    hypothesis = store.load_human_hypothesis()
    ensure_hypothesis_approved(hypothesis)
    _check_allowed_state(
        run_state,
        {
            PipelineState.HYPOTHESIS_APPROVED,
            PipelineState.STRATEGY_DRAFT_READY,
            PipelineState.AWAITING_STRATEGY_REVIEW,
        },
        command="화면⑤ 전략 초안 생성",
    )
    manifest = store.load_run_manifest()

    llm_config = load_llm_config()
    client = create_llm_client(llm_config, settings)
    draft, metadata = draft_strategy(
        hypothesis,
        stock_code=manifest.stock_code,
        client=client,
        prompts_dir=DEFAULT_PROMPTS_DIR,
        max_attempts=llm_config.max_attempts,
    )

    store.save_strategy_draft(draft)
    store.append_ai_usage(
        AIUsageRecord(
            usage_id=f"usage-strategy_translation-{datetime.now(KST):%Y%m%d%H%M%S}",
            stage="strategy_translation",
            model=metadata.model,
            prompt_name="strategy_translation",
            prompt_version="v1",
            input_artifact_ids=["human_investment_hypothesis.json"],
            output_artifact_ids=["strategy_draft.json"],
            ai_role="전략 초안 변환",
            human_review_required=True,
            created_at=now_kst_iso(),
        )
    )

    if run_state.current_state == PipelineState.HYPOTHESIS_APPROVED:
        run_state = advance(run_state, PipelineState.STRATEGY_DRAFT_READY, actor="system")
        run_state = advance(run_state, PipelineState.AWAITING_STRATEGY_REVIEW, actor="system")
        store.save_run_state(run_state)

    return draft, metadata, run_state


def _advance_strategy_review(run_state: RunState, *, actor: str) -> RunState:
    """hitl_flow.py `_advance_strategy_review`와 동일한 회귀 후 재전진 규칙(§5.5)."""
    if run_state.current_state == PipelineState.STRATEGY_APPROVED:
        run_state = advance(
            run_state, PipelineState.AWAITING_STRATEGY_REVIEW, actor=actor, note="전략 재검토"
        )
    return advance(run_state, PipelineState.STRATEGY_APPROVED, actor=actor)


def approve_strategy_action(
    store: RunStore,
    run_state: RunState,
    *,
    final_strategy: dict[str, object],
    approved_by: str,
    approval_reason: str,
    modification_reason: str,
) -> RunState:
    """approve-strategy 재현(hitl_flow.py, §5.5).

    CLI는 사용자가 올린 ``StrategyReview`` JSON의 ``modifications``가
    ``diff_strategies(draft, final)``와 field_path 집합이 일치하는지
    사후 검사한다. 이 화면은 ``modifications``를 직접
    ``diff_strategies(draft, final_strategy, modified_by=approved_by)``\\ 로
    **구성**하므로(사용자가 임의 JSON을 올리는 경로가 없다) 그 불일치가
    애초에 발생할 수 없다 — 동일 제약을 사후 검사 대신 구성으로 보장한다.
    나머지(전략 재검증 ``parse_strategy_spec``→``compile_strategy``, 승인
    주체 필수)는 CLI와 동일하게 다시 수행한다.
    """
    _check_allowed_state(
        run_state,
        {PipelineState.AWAITING_STRATEGY_REVIEW, PipelineState.STRATEGY_APPROVED},
        command="화면⑤ 전략 승인",
    )
    hypothesis = store.load_human_hypothesis()
    ensure_hypothesis_approved(hypothesis)
    draft = store.load_strategy_draft()

    modifications = diff_strategies(draft, final_strategy, modified_by=approved_by)
    if modification_reason.strip():
        modifications = [
            m.model_copy(update={"reason": modification_reason}) for m in modifications
        ]

    review = StrategyReview(
        review_id=f"review-{run_state.run_id}",
        hypothesis_id=hypothesis.hypothesis_id,
        llm_draft_strategy=draft,
        final_strategy=final_strategy,
        modifications=modifications,
        approval_reason=approval_reason,
        approved_by=approved_by,
        approved_at=now_kst_iso(),
    )

    # 승인본 재검증 — approve-strategy와 동일 체인(§5.5).
    spec = parse_strategy_spec(review.final_strategy)
    compile_strategy(spec)

    store.save_strategy_review(review)
    strategy_spec_path = store.run_dir / "strategy_spec.json"
    strategy_spec_path.write_text(
        json.dumps(review.final_strategy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    run_state = _advance_strategy_review(run_state, actor="user")
    store.save_run_state(run_state)
    return run_state


# ---------------------------------------------------------------------------
# 화면⑥ — backtest (app/commands/backtest_cmd.py, §4.4)
# ---------------------------------------------------------------------------


def run_backtest_action(
    settings: Settings,
    store: RunStore,
    run_state: RunState,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    fs_scope: str = "CFS",
    benchmark: str | None = None,
) -> tuple[BacktestResult, RunState]:
    """backtest 재현(app/commands/backtest_cmd.py, §4.4).

    조립 순서: COMPLETE 재백테스트 거부 → ``ensure_state_at_least
    (STRATEGY_APPROVED)`` → 가설·전략 리뷰 정합 검사(``ensure_hypothesis_
    approved`` 포함) → ``load_backtest_config`` → ``execute_approved_strategy``
    (산출물 3종 저장은 runner 내부 책임) → (STRATEGY_APPROVED에서만) 상태 2단계
    전진.
    """
    if run_state.current_state == PipelineState.COMPLETE:
        raise ApprovalGateError(
            "해석까지 완료된 실행은 재백테스트하지 않습니다 — 새 run을 권장합니다."
        )
    ensure_state_at_least(run_state, PipelineState.STRATEGY_APPROVED)

    hypothesis = store.load_human_hypothesis()
    ensure_hypothesis_approved(hypothesis)
    review = store.load_strategy_review()
    ensure_strategy_approved(review)
    if review.hypothesis_id != hypothesis.hypothesis_id:
        raise DataValidationError(
            "전략 리뷰의 hypothesis_id가 승인 가설과 일치하지 않습니다: "
            f"review={review.hypothesis_id!r} vs hypothesis={hypothesis.hypothesis_id!r}."
        )

    manifest = store.load_run_manifest()
    start = start_date or DEFAULT_BACKTEST_START_DATE
    as_of = date.fromisoformat(manifest.as_of_date)
    end = end_date or as_of
    if start > end:
        raise DataValidationError(f"시작일({start})은 종료일({end})보다 클 수 없습니다.")

    config: BacktestConfig = load_backtest_config()
    if benchmark:
        config = config.model_copy(update={"benchmark": benchmark})

    result = execute_approved_strategy(
        review,
        data_dir=settings.data_dir,
        stock_code=manifest.stock_code,
        corp_code=manifest.corp_code,
        start_date=start,
        end_date=end,
        out_dir=store.run_dir,
        backtest_config=config,
        fs_scope=fs_scope,
    )

    if run_state.current_state == PipelineState.STRATEGY_APPROVED:
        run_state = advance(
            run_state,
            PipelineState.BACKTEST_COMPLETE,
            actor="system",
            note=f"{start}~{end} {result.strategy_name}",
        )
        run_state = advance(
            run_state,
            PipelineState.AWAITING_INTERPRETATION,
            actor="system",
            note="사용자 해석 대기",
        )
        store.save_run_state(run_state)

    return result, run_state


# ---------------------------------------------------------------------------
# 화면⑦ — submit-interpretation (hitl_flow.py, §5.6)
# ---------------------------------------------------------------------------


def _apply_hypothesis_decision(
    hypothesis: HumanInvestmentHypothesis, interpretation: BacktestInterpretation
) -> HumanInvestmentHypothesis:
    """가설 판정을 status에 반영(hitl_flow.py `_apply_hypothesis_decision`과 동일, §5.6)."""
    new_status = _DECISION_TO_STATUS[interpretation.hypothesis_decision]
    payload = hypothesis.model_dump(mode="json")
    payload.update(status=new_status.value, updated_at=now_kst_iso())
    return HumanInvestmentHypothesis.model_validate(payload)


def _advance_interpretation(run_state: RunState, *, actor: str) -> RunState:
    """hitl_flow.py `_advance_interpretation`과 동일한 회귀 후 재전진 규칙(§5.6)."""
    if run_state.current_state == PipelineState.COMPLETE:
        run_state = advance(
            run_state, PipelineState.AWAITING_INTERPRETATION, actor=actor, note="해석 재제출"
        )
    return advance(run_state, PipelineState.COMPLETE, actor=actor)


def submit_interpretation_action(
    store: RunStore, run_state: RunState, interpretation: BacktestInterpretation
) -> RunState:
    """submit-interpretation 재현(hitl_flow.py, §5.6).

    조립 순서: 허용 상태 검사 → hypothesis_id·strategy_id 정합 검사 → 가설
    판정 반영(``_apply_hypothesis_decision``) → ``validate_hypothesis``
    재검증 → 저장(interpretation·갱신된 hypothesis) → 상태 전진(COMPLETE).
    """
    _check_allowed_state(
        run_state,
        {PipelineState.AWAITING_INTERPRETATION, PipelineState.COMPLETE},
        command="화면⑦ 결과 해석 제출",
    )
    hypothesis = store.load_human_hypothesis()
    if interpretation.hypothesis_id != hypothesis.hypothesis_id:
        raise DataValidationError(
            f"interpretation.hypothesis_id({interpretation.hypothesis_id})가 저장된 가설"
            f"({hypothesis.hypothesis_id})과 일치하지 않습니다."
        )
    strategy_name = load_strategy_name(store)
    if interpretation.strategy_id != strategy_name:
        raise DataValidationError(
            f"interpretation.strategy_id({interpretation.strategy_id})가 strategy_spec.json"
            f"의 strategy_name({strategy_name})과 일치하지 않습니다."
        )

    updated_hypothesis = _apply_hypothesis_decision(hypothesis, interpretation)
    evidence_store = FileEvidenceStore.from_manifest(store.run_dir / "evidence_manifest.json")
    validate_hypothesis(
        updated_hypothesis,
        evidence_store,
        _supported_variables(updated_hypothesis.selected_variables),
    )

    store.save_backtest_interpretation(interpretation)
    store.save_human_hypothesis(updated_hypothesis)

    run_state = _advance_interpretation(run_state, actor="user")
    store.save_run_state(run_state)
    return run_state


__all__ = [
    "DEFAULT_BACKTEST_START_DATE",
    "HYPOTHESIS_DECISION_OPTIONS",
    "CandidateGenerationResult",
    "CreateRunResult",
    "ResolveFailure",
    "approve_hypothesis_draft",
    "approve_strategy_action",
    "create_run",
    "ensure_candidates_stage",
    "ensure_data_ready",
    "generate_candidates",
    "generate_strategy_draft_action",
    "load_backtest_result",
    "load_daily_portfolio",
    "load_evidence_manifest_ids",
    "load_robustness_report",
    "load_strategy_name",
    "load_trade_log",
    "resolve_corp",
    "run_backtest_action",
    "save_analyst_view",
    "save_hypothesis",
    "submit_interpretation_action",
    "try_load_analyst_view",
    "try_load_backtest_interpretation",
    "try_load_candidate_analysis",
    "try_load_human_hypothesis",
    "try_load_hypothesis_candidates",
    "try_load_strategy_draft",
    "try_load_strategy_review",
]
