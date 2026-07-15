"""HITL 워크플로 CLI 단위 테스트 (docs/specs/CLI-integration.md §5, §7).

``research_backtest.app.cli``는 건드리지 않는다 — ``typer.Typer()`` 새
인스턴스에 ``hitl_flow.register(app)``로 구성해 테스트한다(명세 §7).
DART·재무·시장 계층은 전부 mock이며, 각 명령은 정상 경로 1개 이상 +
실패 경로(게이트 exit 4 / 검증 exit 1 / 부재 exit 1) 1개 이상을 검증한다.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from research_backtest.app.commands import hitl_flow
from research_backtest.core.config import DartConfig, Settings
from research_backtest.core.dart.corp_code import CorpCodeRegistry
from research_backtest.core.financials.pipeline import METRICS_FILENAME, financials_out_dir
from research_backtest.core.hitl.models import AnalystView, HumanInvestmentHypothesis
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    advance,
    create_run_state,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.market.collector import (
    DAILY_FILENAME,
    market_calendar_path,
    market_normalized_stock_dir,
)
from research_backtest.core.models import DartCorporation

runner = CliRunner()

SK_HYNIX = DartCorporation(
    corp_code="00164779",
    corp_name="SK하이닉스",
    corp_eng_name="SK hynix Inc.",
    stock_code="000660",
    modify_date="20250102",
)
UNLISTED = DartCorporation(
    corp_code="99999999",
    corp_name="비상장테스트",
    corp_eng_name=None,
    stock_code=None,
    modify_date="20250102",
)


def _build_app() -> typer.Typer:
    application = typer.Typer()
    hitl_flow.register(application)
    return application


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """rich 표가 비-TTY 테스트 환경(기본 width=80)에서 잘리지 않도록 폭을 넓힌다."""
    monkeypatch.setattr(hitl_flow, "console", Console(width=200))


def _make_settings(tmp_path: Path, *, dart_api_key: str = "unit-test-key") -> Settings:
    return Settings(
        _env_file=None,
        dart_api_key=dart_api_key,
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
    )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    active = _make_settings(tmp_path)
    monkeypatch.setattr(hitl_flow, "get_settings", lambda: active)
    return active


def _patch_dart_layers(
    monkeypatch: pytest.MonkeyPatch, corporations: list[DartCorporation]
) -> None:
    """A2 CLI 테스트 관례(test_cli_collect_market.py) 복제 — DART 계층 전부 mock."""
    monkeypatch.setattr(hitl_flow, "load_dart_config", lambda: DartConfig())
    registry = CorpCodeRegistry(corporations)

    def fake_load_registry(
        client: Any, cache_dir: Path, *, refresh_days: int, force: bool = False, now: Any = None
    ) -> CorpCodeRegistry:
        return registry

    monkeypatch.setattr(hitl_flow, "load_corp_code_registry", fake_load_registry)


def _mark_data_ready(settings_obj: Settings, corp: DartCorporation) -> None:
    """create-run의 데이터 준비 검사(§5.1)를 통과시키기 위한 빈 parquet 파일 3종."""
    assert corp.stock_code is not None
    metrics_path = financials_out_dir(settings_obj.data_dir, corp.corp_code) / METRICS_FILENAME
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_bytes(b"")

    daily_path = market_normalized_stock_dir(settings_obj.data_dir, corp.stock_code) / (
        DAILY_FILENAME
    )
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_bytes(b"")

    calendar_path = market_calendar_path(settings_obj.data_dir)
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    calendar_path.write_bytes(b"")


def _write_json(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _write_evidence_manifest(run_dir: Path, evidence_ids: list[str]) -> None:
    payload = {"evidence": [{"evidence_id": eid} for eid in evidence_ids]}
    _write_json(run_dir / "evidence_manifest.json", payload)


def _make_run(
    outputs_dir: Path,
    run_id: str,
    *,
    state: PipelineState,
    company: str = "SK하이닉스",
    as_of_date: str = "2025-12-31",
) -> RunStore:
    """지정한 ``state``까지 정방향으로만 전진시킨 run_state.json을 픽스처로 생성한다.

    이 헬퍼가 만드는 전이는 테스트 셋업용(actor="test-fixture")이며, 실제로
    검증하려는 명령의 동작과는 무관하다 — 명령별 산출물(analyst_view.json 등)은
    각 테스트가 필요에 따라 별도로 저장한다.
    """
    store = RunStore(outputs_dir, run_id)
    run_state = create_run_state(run_id, company, as_of_date, actor="test-fixture")
    target_idx = FORWARD_ORDER.index(state)
    for target in FORWARD_ORDER[1 : target_idx + 1]:
        run_state = advance(run_state, target, actor="test-fixture")
    store.save_run_state(run_state)
    return store


def _valid_analyst_view(**overrides: Any) -> AnalystView:
    payload: dict[str, Any] = {
        "view_id": "VIEW-1",
        "author": "홍길동",
        "research_question": "실적 회복은 주가에 선반영되었는가?",
        "core_thesis": "서프라이즈 여부가 향후 주가를 결정한다.",
        "selected_evidence_ids": ["EVID-001", "EVID-002"],
        "rejected_evidence_ids": [],
        "evidence_selection_reason": "1차 공시 자료를 우선한다.",
        "rejected_evidence_reasons": {},
        "interpretation": "HBM 비중 확대가 핵심이다.",
        "expected_mechanism": "ASP 상승 → 이익률 개선",
        "counterarguments": ["이미 선반영되었을 수 있다."],
        "uncertainties": [],
        "created_at": "2026-07-14T10:00:00+09:00",
        "updated_at": "2026-07-14T10:00:00+09:00",
    }
    payload.update(overrides)
    return AnalystView.model_validate(payload)


def _hypothesis_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hypothesis_id": "HYP-1",
        "view_id": "VIEW-1",
        "author": "홍길동",
        "thesis": "HBM 비중 확대가 이익률을 컨센서스 이상으로 끌어올린다.",
        "economic_rationale": "HBM 마진이 legacy 대비 높다.",
        "expected_mechanism": "ASP 상승 → 이익률 개선",
        "selected_variables": ["operating_income_yoy"],
        "expected_direction": "up",
        "investment_horizon_days": 90,
        "evidence_ids": ["EVID-001"],
        "falsification_conditions": ["2개 분기 연속 컨센서스 하회 시 기각"],
        "limitations": [],
        "status": "DRAFT",
        "created_at": "2026-07-14T11:00:00+09:00",
        "updated_at": "2026-07-14T11:00:00+09:00",
    }
    payload.update(overrides)
    return payload


def _demo_strategy(right: float) -> dict[str, Any]:
    return {
        "strategy_name": "demo_strategy",
        "version": "1.0",
        "universe": {"type": "single_asset", "tickers": ["000660"]},
        "entry": {"all": [{"left": "operating_income_yoy", "operator": ">", "right": right}]},
        "exit": {"any": [{"type": "max_holding_days", "value": 60}]},
        "execution": {"signal_time": "close", "trade_time": "next_open"},
    }


def _approved_hypothesis_payload(**overrides: Any) -> dict[str, Any]:
    payload = _hypothesis_payload(
        status="APPROVED",
        approved_by="user",
        approved_at="2026-07-14T12:00:00+09:00",
    )
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# create-run (§5.1)
# ---------------------------------------------------------------------------


def test_create_run_success(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    _mark_data_ready(settings, SK_HYNIX)

    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == 0, result.output
    assert "run 생성 완료" in result.output
    assert "파이프라인 상태: DATA_READY" in result.output
    assert "다음 단계: generate-candidates" in result.output

    run_dirs = list(settings.outputs_dir.iterdir())
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name

    store = RunStore(settings.outputs_dir, run_id)
    manifest = store.load_run_manifest()
    assert manifest.corp_code == "00164779"
    assert manifest.stock_code == "000660"
    assert manifest.as_of_date == "2025-12-31"
    assert manifest.company_query == "SK하이닉스"

    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.DATA_READY
    assert run_state.transitions[-1].actor == "user"
    assert run_state.transitions[-1].auto_approved is False


def test_create_run_missing_dart_key_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_key_settings = _make_settings(tmp_path, dart_api_key="")
    monkeypatch.setattr(hitl_flow, "get_settings", lambda: no_key_settings)
    _patch_dart_layers(monkeypatch, [SK_HYNIX])

    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == 3
    assert "DART_API_KEY" in result.output


def test_create_run_unlisted_company_exits_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dart_layers(monkeypatch, [UNLISTED])
    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "비상장테스트", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == 1
    assert "비상장" in result.output


def test_create_run_missing_financials_exits_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    # 재무 지표 없이 시장 데이터만 준비 — build-financials 안내가 나와야 한다.
    daily_path = market_normalized_stock_dir(settings.data_dir, "000660") / DAILY_FILENAME
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    daily_path.write_bytes(b"")
    calendar_path = market_calendar_path(settings.data_dir)
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    calendar_path.write_bytes(b"")

    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == 1
    assert "build-financials" in result.output


def test_create_run_missing_market_data_exits_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    metrics_path = financials_out_dir(settings.data_dir, "00164779") / METRICS_FILENAME
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_bytes(b"")

    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    assert result.exit_code == 1
    assert "collect-market" in result.output


def test_create_run_bad_date_format_rejected(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    result = runner.invoke(
        _build_app(),
        ["create-run", "--company", "SK하이닉스", "--as-of-date", "2025/12/31"],
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# runs (§5.2)
# ---------------------------------------------------------------------------


def test_runs_empty_outputs_dir_exits_0(settings: Settings) -> None:
    result = runner.invoke(_build_app(), ["runs"])
    assert result.exit_code == 0
    assert "create-run" in result.output


def test_runs_lists_valid_run_and_ignores_invalid_dir(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "20260715_090000_SKHYNIX", state=PipelineState.DATA_READY)
    stray_dir = settings.outputs_dir / "not_a_run"
    stray_dir.mkdir(parents=True)
    (stray_dir / "readme.txt").write_text("stray", encoding="utf-8")

    result = runner.invoke(_build_app(), ["runs"])
    assert result.exit_code == 0
    assert "20260715_090000_SKHYNIX" in result.output
    assert "DATA_READY" in result.output
    assert "1개 무시" in result.output


# ---------------------------------------------------------------------------
# status (§5.2)
# ---------------------------------------------------------------------------


def test_status_valid_run_shows_footer(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-1", state=PipelineState.DATA_READY)
    result = runner.invoke(_build_app(), ["status", "--run-id", "RUN-1"])
    assert result.exit_code == 0
    assert "파이프라인 상태: DATA_READY  (run: RUN-1)" in result.output
    assert "다음 단계: generate-candidates" in result.output
    assert "run_manifest.json" in result.output  # 산출물 체크리스트


def test_status_missing_run_exits_1(settings: Settings) -> None:
    result = runner.invoke(_build_app(), ["status", "--run-id", "NO-SUCH-RUN"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# create-analyst-view (§5.3)
# ---------------------------------------------------------------------------


def test_create_analyst_view_success(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(settings.outputs_dir, "RUN-AV", state=PipelineState.AWAITING_ANALYST_VIEW)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002", "EVID-003"])
    input_path = _write_json(tmp_path / "analyst_view.json", _valid_analyst_view().model_dump())

    result = runner.invoke(
        _build_app(),
        ["create-analyst-view", "--run-id", "RUN-AV", "--input", str(input_path)],
    )
    assert result.exit_code == 0, result.output

    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.ANALYST_VIEW_APPROVED
    assert run_state.transitions[-1].actor == "user"
    assert run_state.transitions[-1].auto_approved is False
    assert store.load_analyst_view().view_id == "VIEW-1"


def test_create_analyst_view_resubmission_advances_twice(
    settings: Settings, tmp_path: Path
) -> None:
    store = _make_run(settings.outputs_dir, "RUN-AV2", state=PipelineState.ANALYST_VIEW_APPROVED)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    before = store.load_run_state()
    input_path = _write_json(tmp_path / "analyst_view.json", _valid_analyst_view().model_dump())

    result = runner.invoke(
        _build_app(),
        ["create-analyst-view", "--run-id", "RUN-AV2", "--input", str(input_path)],
    )
    assert result.exit_code == 0, result.output
    after = store.load_run_state()
    assert after.current_state == PipelineState.ANALYST_VIEW_APPROVED
    assert len(after.transitions) == len(before.transitions) + 2
    assert after.transitions[-2].to_state == PipelineState.AWAITING_ANALYST_VIEW


def test_create_analyst_view_missing_evidence_manifest_exits_1(
    settings: Settings, tmp_path: Path
) -> None:
    _make_run(settings.outputs_dir, "RUN-AV3", state=PipelineState.AWAITING_ANALYST_VIEW)
    input_path = _write_json(tmp_path / "analyst_view.json", _valid_analyst_view().model_dump())

    result = runner.invoke(
        _build_app(),
        ["create-analyst-view", "--run-id", "RUN-AV3", "--input", str(input_path)],
    )
    assert result.exit_code == 1
    assert "generate-candidates" in result.output


def test_create_analyst_view_wrong_state_exits_4(settings: Settings, tmp_path: Path) -> None:
    _make_run(settings.outputs_dir, "RUN-AV4", state=PipelineState.DATA_READY)
    input_path = _write_json(tmp_path / "analyst_view.json", _valid_analyst_view().model_dump())

    result = runner.invoke(
        _build_app(),
        ["create-analyst-view", "--run-id", "RUN-AV4", "--input", str(input_path)],
    )
    assert result.exit_code == 4


def test_create_analyst_view_missing_input_file_exits_1(settings: Settings) -> None:
    store = _make_run(settings.outputs_dir, "RUN-AV5", state=PipelineState.AWAITING_ANALYST_VIEW)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])

    result = runner.invoke(
        _build_app(),
        ["create-analyst-view", "--run-id", "RUN-AV5", "--input", "/no/such/file.json"],
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# create-hypothesis (§5.4)
# ---------------------------------------------------------------------------


def test_create_hypothesis_draft_success(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(settings.outputs_dir, "RUN-HYP", state=PipelineState.ANALYST_VIEW_APPROVED)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_analyst_view(_valid_analyst_view())
    input_path = _write_json(tmp_path / "hypothesis.json", _hypothesis_payload(status="DRAFT"))

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP", "--input", str(input_path)],
    )
    assert result.exit_code == 0, result.output
    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.HYPOTHESIS_DRAFT
    assert run_state.transitions[-1].auto_approved is False


def test_create_hypothesis_approved_from_draft(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(settings.outputs_dir, "RUN-HYP2", state=PipelineState.HYPOTHESIS_DRAFT)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_analyst_view(_valid_analyst_view())
    input_path = _write_json(tmp_path / "hypothesis.json", _approved_hypothesis_payload())

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP2", "--input", str(input_path)],
    )
    assert result.exit_code == 0, result.output
    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.HYPOTHESIS_APPROVED
    saved = store.load_human_hypothesis()
    assert saved.approved_by == "user"


def test_create_hypothesis_view_id_mismatch_exits_1(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(settings.outputs_dir, "RUN-HYP3", state=PipelineState.ANALYST_VIEW_APPROVED)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_analyst_view(_valid_analyst_view(view_id="VIEW-1"))
    input_path = _write_json(
        tmp_path / "hypothesis.json", _hypothesis_payload(view_id="VIEW-OTHER")
    )

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP3", "--input", str(input_path)],
    )
    assert result.exit_code == 1


def test_create_hypothesis_wrong_state_exits_4(settings: Settings, tmp_path: Path) -> None:
    _make_run(settings.outputs_dir, "RUN-HYP4", state=PipelineState.DATA_READY)
    input_path = _write_json(tmp_path / "hypothesis.json", _hypothesis_payload())

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP4", "--input", str(input_path)],
    )
    assert result.exit_code == 4


def test_create_hypothesis_tested_status_rejected_exits_1(
    settings: Settings, tmp_path: Path
) -> None:
    store = _make_run(settings.outputs_dir, "RUN-HYP5", state=PipelineState.ANALYST_VIEW_APPROVED)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_analyst_view(_valid_analyst_view())
    input_path = _write_json(tmp_path / "hypothesis.json", _hypothesis_payload(status="TESTED"))

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP5", "--input", str(input_path)],
    )
    assert result.exit_code == 1
    assert "DRAFT/APPROVED" in result.output


def test_create_hypothesis_unsupported_variable_exits_1(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(settings.outputs_dir, "RUN-HYP6", state=PipelineState.ANALYST_VIEW_APPROVED)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_analyst_view(_valid_analyst_view())
    input_path = _write_json(
        tmp_path / "hypothesis.json",
        _hypothesis_payload(selected_variables=["not_a_real_indicator"]),
    )

    result = runner.invoke(
        _build_app(),
        ["create-hypothesis", "--run-id", "RUN-HYP6", "--input", str(input_path)],
    )
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# approve-strategy (§5.5)
# ---------------------------------------------------------------------------


def test_approve_strategy_success(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(
        settings.outputs_dir, "RUN-STRAT", state=PipelineState.AWAITING_STRATEGY_REVIEW
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_approved_hypothesis_payload())
    )
    draft = _demo_strategy(right=0.1)
    final = _demo_strategy(right=0.2)
    store.save_strategy_draft(draft)

    from research_backtest.core.hitl.diff import diff_strategies

    modifications = [
        m.model_dump(mode="json") for m in diff_strategies(draft, final, modified_by="user")
    ]
    review_payload = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": draft,
        "final_strategy": final,
        "modifications": modifications,
        "approval_reason": "임계값을 보수적으로 조정",
        "approved_by": "user",
        "approved_at": "2026-07-15T10:00:00+09:00",
    }
    review_path = _write_json(tmp_path / "review.json", review_payload)

    result = runner.invoke(
        _build_app(),
        ["approve-strategy", "--run-id", "RUN-STRAT", "--review", str(review_path)],
    )
    assert result.exit_code == 0, result.output
    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.STRATEGY_APPROVED
    assert (store.run_dir / "strategy_spec.json").exists()
    saved_spec = json.loads((store.run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    assert saved_spec == final


def test_approve_strategy_hypothesis_not_approved_exits_4(
    settings: Settings, tmp_path: Path
) -> None:
    store = _make_run(
        settings.outputs_dir, "RUN-STRAT2", state=PipelineState.AWAITING_STRATEGY_REVIEW
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_hypothesis_payload(status="DRAFT"))
    )
    draft = _demo_strategy(right=0.1)
    store.save_strategy_draft(draft)
    review_payload = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": draft,
        "final_strategy": draft,
        "modifications": [],
        "approval_reason": "그대로 승인",
        "approved_by": "user",
        "approved_at": "2026-07-15T10:00:00+09:00",
    }
    review_path = _write_json(tmp_path / "review.json", review_payload)

    result = runner.invoke(
        _build_app(),
        ["approve-strategy", "--run-id", "RUN-STRAT2", "--review", str(review_path)],
    )
    assert result.exit_code == 4


def test_approve_strategy_modifications_mismatch_exits_1(
    settings: Settings, tmp_path: Path
) -> None:
    store = _make_run(
        settings.outputs_dir, "RUN-STRAT3", state=PipelineState.AWAITING_STRATEGY_REVIEW
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_approved_hypothesis_payload())
    )
    draft = _demo_strategy(right=0.1)
    final = _demo_strategy(right=0.2)
    store.save_strategy_draft(draft)
    review_payload = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": draft,
        "final_strategy": final,
        "modifications": [],  # 실제 diff(entry.all[0].right)가 누락됨
        "approval_reason": "임계값을 보수적으로 조정",
        "approved_by": "user",
        "approved_at": "2026-07-15T10:00:00+09:00",
    }
    review_path = _write_json(tmp_path / "review.json", review_payload)

    result = runner.invoke(
        _build_app(),
        ["approve-strategy", "--run-id", "RUN-STRAT3", "--review", str(review_path)],
    )
    assert result.exit_code == 1
    assert "modifications" in result.output


def test_approve_strategy_draft_tampered_exits_1(settings: Settings, tmp_path: Path) -> None:
    store = _make_run(
        settings.outputs_dir, "RUN-STRAT4", state=PipelineState.AWAITING_STRATEGY_REVIEW
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_approved_hypothesis_payload())
    )
    store.save_strategy_draft(_demo_strategy(right=0.1))
    review_payload = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": _demo_strategy(right=0.05),  # 저장된 draft와 다름 — 위변조
        "final_strategy": _demo_strategy(right=0.2),
        "modifications": [],
        "approval_reason": "임계값을 보수적으로 조정",
        "approved_by": "user",
        "approved_at": "2026-07-15T10:00:00+09:00",
    }
    review_path = _write_json(tmp_path / "review.json", review_payload)

    result = runner.invoke(
        _build_app(),
        ["approve-strategy", "--run-id", "RUN-STRAT4", "--review", str(review_path)],
    )
    assert result.exit_code == 1
    assert "위변조" in result.output


def test_approve_strategy_wrong_state_exits_4(settings: Settings, tmp_path: Path) -> None:
    _make_run(settings.outputs_dir, "RUN-STRAT5", state=PipelineState.HYPOTHESIS_APPROVED)
    review_path = _write_json(
        tmp_path / "review.json",
        {
            "review_id": "REVIEW-1",
            "hypothesis_id": "HYP-1",
            "llm_draft_strategy": {},
            "final_strategy": {},
            "modifications": [],
            "approval_reason": "ok",
            "approved_by": "user",
            "approved_at": "2026-07-15T10:00:00+09:00",
        },
    )
    result = runner.invoke(
        _build_app(),
        ["approve-strategy", "--run-id", "RUN-STRAT5", "--review", str(review_path)],
    )
    assert result.exit_code == 4


# ---------------------------------------------------------------------------
# submit-interpretation (§5.6)
# ---------------------------------------------------------------------------


def _interpretation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "interpretation_id": "INTERP-1",
        "hypothesis_id": "HYP-1",
        "strategy_id": "demo_strategy",
        "author": "홍길동",
        "main_findings": "가설과 일치하는 초과수익을 관측했다.",
        "supporting_results": ["누적수익률이 벤치마크를 상회"],
        "contradicting_results": [],
        "regime_dependence": None,
        "limitations": [],
        "hypothesis_decision": "SUPPORTED",
        "decision_reason": "컨센서스 상회 분기가 반복적으로 관측되었다.",
        "revised_hypothesis": None,
        "followup_tests": [],
        "created_at": "2026-07-15T12:00:00+09:00",
    }
    payload.update(overrides)
    return payload


def _setup_interpretation_run(settings_obj: Settings, run_id: str) -> RunStore:
    store = _make_run(settings_obj.outputs_dir, run_id, state=PipelineState.AWAITING_INTERPRETATION)
    _write_evidence_manifest(store.run_dir, ["EVID-001", "EVID-002"])
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_approved_hypothesis_payload())
    )
    _write_json(store.run_dir / "strategy_spec.json", _demo_strategy(right=0.2))
    return store


def test_submit_interpretation_success(settings: Settings, tmp_path: Path) -> None:
    store = _setup_interpretation_run(settings, "RUN-INTERP")
    input_path = _write_json(tmp_path / "interpretation.json", _interpretation_payload())

    result = runner.invoke(
        _build_app(),
        ["submit-interpretation", "--run-id", "RUN-INTERP", "--input", str(input_path)],
    )
    assert result.exit_code == 0, result.output
    run_state = store.load_run_state()
    assert run_state.current_state == PipelineState.COMPLETE
    updated_hypothesis = store.load_human_hypothesis()
    assert updated_hypothesis.status.value == "SUPPORTED"


def test_submit_interpretation_strategy_id_mismatch_exits_1(
    settings: Settings, tmp_path: Path
) -> None:
    _setup_interpretation_run(settings, "RUN-INTERP2")
    input_path = _write_json(
        tmp_path / "interpretation.json",
        _interpretation_payload(strategy_id="other_strategy"),
    )

    result = runner.invoke(
        _build_app(),
        ["submit-interpretation", "--run-id", "RUN-INTERP2", "--input", str(input_path)],
    )
    assert result.exit_code == 1


def test_submit_interpretation_wrong_state_exits_4(settings: Settings, tmp_path: Path) -> None:
    _make_run(settings.outputs_dir, "RUN-INTERP3", state=PipelineState.STRATEGY_APPROVED)
    input_path = _write_json(tmp_path / "interpretation.json", _interpretation_payload())

    result = runner.invoke(
        _build_app(),
        ["submit-interpretation", "--run-id", "RUN-INTERP3", "--input", str(input_path)],
    )
    assert result.exit_code == 4


# ---------------------------------------------------------------------------
# 상태 인지형 스텁 3종 (§5.7)
# ---------------------------------------------------------------------------


def test_generate_candidates_not_yet_ready_exits_2(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-GC1", state=PipelineState.DATA_READY)
    result = runner.invoke(_build_app(), ["generate-candidates", "--run-id", "RUN-GC1"])
    assert result.exit_code == 2
    assert "C1'" in result.output
    assert "이미 생성된 실행" not in result.output


def test_generate_candidates_already_generated_warns_then_exits_2(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-GC2", state=PipelineState.AWAITING_ANALYST_VIEW)
    result = runner.invoke(_build_app(), ["generate-candidates", "--run-id", "RUN-GC2"])
    assert result.exit_code == 2
    assert "이미 생성된 실행" in result.output


def test_generate_candidates_missing_run_exits_1(settings: Settings) -> None:
    result = runner.invoke(_build_app(), ["generate-candidates", "--run-id", "NO-SUCH-RUN"])
    assert result.exit_code == 1


def test_generate_strategy_draft_hypothesis_not_reached_exits_4(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-GSD1", state=PipelineState.HYPOTHESIS_DRAFT)
    result = runner.invoke(_build_app(), ["generate-strategy-draft", "--run-id", "RUN-GSD1"])
    assert result.exit_code == 4


def test_generate_strategy_draft_ready_exits_2(settings: Settings) -> None:
    store = _make_run(settings.outputs_dir, "RUN-GSD2", state=PipelineState.HYPOTHESIS_APPROVED)
    store.save_human_hypothesis(
        HumanInvestmentHypothesis.model_validate(_approved_hypothesis_payload())
    )
    result = runner.invoke(_build_app(), ["generate-strategy-draft", "--run-id", "RUN-GSD2"])
    assert result.exit_code == 2
    assert "C2'" in result.output


def test_generate_report_not_complete_exits_4(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-GR1", state=PipelineState.AWAITING_INTERPRETATION)
    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR1"])
    assert result.exit_code == 4


def test_generate_report_complete_exits_2(settings: Settings) -> None:
    _make_run(settings.outputs_dir, "RUN-GR2", state=PipelineState.COMPLETE)
    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR2"])
    assert result.exit_code == 2
    assert "C3'" in result.output
