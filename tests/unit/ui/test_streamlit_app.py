"""Streamlit 7화면 AppTest 스모크 + 화면③ CLI 동등성 테스트 (docs/specs/W3c-report-ui.md §3.2).

LLM 호출 경로(화면②·⑤ 생성 버튼)는 이 테스트들에서 아예 누르지 않는다 —
스모크 케이스는 픽스처 run의 상태별 위젯 존재·잠금만 확인하고, 화면③
라운드트립 케이스는 LLM을 쓰지 않는 create-analyst-view 경로만 검증한다
(live LLM 호출 예산 0회, 명세 §4). ``FakeLlmClient``\\ 는 이 파일에서 직접
쓰이진 않지만, 만약 생성 버튼을 누르는 테스트를 추가한다면
``monkeypatch.setattr(actions, "create_llm_client", lambda cfg, s: FakeLlmClient([...]))``\\
패턴을 따른다(``tests/unit/test_cli_strategy_draft.py``\\ 와 동일 관례).
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from research_backtest.app.ui import state as ui_state
from research_backtest.core.config import Settings
from research_backtest.core.hitl.states import PipelineState
from research_backtest.core.hitl.store import RunStore

from .conftest import (
    CORP_NAME,
    EVIDENCE_IDS,
    make_analyst_view,
    make_backtest_interpretation,
    make_backtest_result,
    make_candidate_analysis,
    make_hypothesis,
    make_run_store,
    make_strategy,
    make_strategy_review,
    write_backtest_artifacts,
    write_evidence_manifest,
)

APP_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "research_backtest" / "app" / "streamlit_app.py"
)


def _run_option(run_id: str, state_value: PipelineState) -> str:
    return f"{run_id} — {CORP_NAME} [{ui_state.PIPELINE_STATE_LABELS[state_value]}]"


def _has_key(widgets: object, key: str) -> bool:
    return any(getattr(w, "key", None) == key for w in widgets)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 스모크 1/3 — run 없음
# ---------------------------------------------------------------------------


def test_app_loads_with_no_runs(ui_settings: Settings) -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert not at.exception
    assert _has_key(at.text_input, "scr1_company")
    assert any("등록된 run이 없습니다" in info.value for info in at.info)
    assert any("먼저 사이드바에서 run을 선택" in info.value for info in at.info)


# ---------------------------------------------------------------------------
# 스모크 2/3 — DATA_READY (아직 후보 없음 → 화면②만 활성, 나머지 잠금)
# ---------------------------------------------------------------------------


def test_screen_state_data_ready(ui_settings: Settings) -> None:
    run_id = "20260101_000000_TESTCO"
    make_run_store(ui_settings, run_id, target_state=PipelineState.DATA_READY)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(_run_option(run_id, PipelineState.DATA_READY))
    at.run()

    assert not at.exception
    assert _has_key(at.button, f"scr2_generate_btn__{run_id}")

    # 화면③~⑦은 아직 진입 불가 — 잠금 사유가 표시되고 폼 위젯은 렌더되지 않는다.
    assert any("AI 분석 후보를 먼저 생성하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr3_view_id__{run_id}")
    assert any("분석 관점을 먼저 저장하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr4_id__{run_id}")
    assert any("백테스트를 먼저 실행하세요" in w.value for w in at.warning)


# ---------------------------------------------------------------------------
# 스모크 3/3 — AWAITING_ANALYST_VIEW (후보 있음 → 화면②③ 활성, 화면④ 잠금)
# ---------------------------------------------------------------------------


def test_screen_state_awaiting_analyst_view(ui_settings: Settings) -> None:
    run_id = "20260101_000001_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.AWAITING_ANALYST_VIEW)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(
        _run_option(run_id, PipelineState.AWAITING_ANALYST_VIEW)
    )
    at.run()

    assert not at.exception
    # 화면② — AI 후보가 이미 있으므로 재생성 버튼 + 후보 statement가 보인다.
    assert _has_key(at.button, f"scr2_generate_btn__{run_id}")
    assert any("영업이익이 흑자로 전환되었다." in md.value for md in at.markdown)

    # 화면③ — 편집 가능한 폼이 렌더되고 잠겨있지 않다.
    view_id_widget = at.text_input(key=f"scr3_view_id__{run_id}")
    assert view_id_widget.disabled is False
    assert _has_key(at.multiselect, f"scr3_selected__{run_id}")

    # 화면④ — 아직 분석 관점이 저장되지 않았으므로 잠금.
    assert any("분석 관점을 먼저 저장하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr4_id__{run_id}")


# ---------------------------------------------------------------------------
# 스모크(추가) — COMPLETE (전체 파이프라인 완료 → 화면⑥⑦ 결과·생성-보고서 안내)
# ---------------------------------------------------------------------------


def test_screen_state_complete(ui_settings: Settings) -> None:
    run_id = "20260101_000002_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.COMPLETE)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())
    store.save_analyst_view(make_analyst_view())
    store.save_human_hypothesis(make_hypothesis())
    draft = make_strategy()
    store.save_strategy_draft(draft)
    store.save_strategy_review(make_strategy_review(draft=draft))
    result = make_backtest_result()
    write_backtest_artifacts(store.run_dir, result)
    store.save_backtest_interpretation(make_backtest_interpretation())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(_run_option(run_id, PipelineState.COMPLETE))
    at.run()

    assert not at.exception

    # 화면② — 이미 분석 관점 이후로 진행되어 재생성 버튼은 없고 읽기 전용 안내만 있다.
    assert not _has_key(at.button, f"scr2_generate_btn__{run_id}")
    assert any("재생성할 수 없습니다" in w.value for w in at.info)

    # 화면③④⑤ — 읽기 전용(폼은 보이되 비활성화, 저장·승인 버튼 없음).
    assert at.text_input(key=f"scr3_view_id__{run_id}").disabled is True
    assert not _has_key(at.button, f"scr3_save_btn__{run_id}")
    assert at.text_input(key=f"scr4_id__{run_id}").disabled is True
    assert not _has_key(at.button, f"scr4_approve_btn__{run_id}")

    # 화면⑥ — 성과지표 표 + 거래내역이 렌더된다.
    assert any("demo_strategy" in md.value for md in at.markdown)
    assert len(at.table) > 0
    assert len(at.dataframe) > 0

    # 화면⑦ — COMPLETE 안내 + generate-report 힌트.
    assert any("COMPLETE" in s.value for s in at.success)
    assert any("generate-report" in c.value for c in at.caption)


# ---------------------------------------------------------------------------
# 라운드트립 — 화면③ 저장이 create-analyst-view와 동일 산출물·전이를 만드는지
# ---------------------------------------------------------------------------


def test_screen3_save_matches_cli_transition(ui_settings: Settings) -> None:
    run_id = "20260101_000003_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.AWAITING_ANALYST_VIEW)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(
        _run_option(run_id, PipelineState.AWAITING_ANALYST_VIEW)
    )
    at.run()

    at.text_input(key=f"scr3_view_id__{run_id}").set_value("view-e2e")
    at.text_input(key=f"scr3_author__{run_id}").set_value("테스트 사용자")
    at.text_area(key=f"scr3_question__{run_id}").set_value("실적 회복은 선반영되었는가?")
    at.text_area(key=f"scr3_thesis__{run_id}").set_value("서프라이즈 여부가 핵심이다.")
    at.multiselect(key=f"scr3_selected__{run_id}").set_value([EVIDENCE_IDS[0], EVIDENCE_IDS[1]])
    at.multiselect(key=f"scr3_rejected__{run_id}").set_value([EVIDENCE_IDS[2]])
    at.run()

    # 제외 근거를 선택했으므로 근거별 이유 입력 위젯이 동적으로 나타난다.
    assert _has_key(at.text_input, f"scr3_rej_reason_{EVIDENCE_IDS[2]}__{run_id}")
    at.text_input(key=f"scr3_rej_reason_{EVIDENCE_IDS[2]}__{run_id}").set_value("이번 범위 밖")
    at.text_area(key=f"scr3_sel_reason__{run_id}").set_value("1차 공시 자료를 우선한다.")
    at.text_area(key=f"scr3_interpretation__{run_id}").set_value("모멘텀이 이어진다.")
    at.text_area(key=f"scr3_mechanism__{run_id}").set_value("확인 → 수급 유입 → 추세 지속")
    at.text_area(key=f"scr3_counter__{run_id}").set_value("이미 선반영되었을 수 있다.")
    at.text_area(key=f"scr3_uncertain__{run_id}").set_value("업황 사이클 판단")
    at.run()

    at.button(key=f"scr3_save_btn__{run_id}").click().run()
    assert not at.exception
    assert any("분석 관점을 저장했습니다" in s.value for s in at.success)

    # --- CLI(create-analyst-view)가 만드는 것과 동일한 산출물·전이인지 검증 ---
    reloaded = RunStore(ui_settings.outputs_dir, run_id)
    run_state = reloaded.load_run_state()
    assert run_state.current_state == PipelineState.ANALYST_VIEW_APPROVED
    last_transition = run_state.transitions[-1]
    assert last_transition.from_state == PipelineState.AWAITING_ANALYST_VIEW
    assert last_transition.to_state == PipelineState.ANALYST_VIEW_APPROVED
    assert last_transition.actor == "user"
    assert last_transition.auto_approved is False

    saved_view = reloaded.load_analyst_view()
    assert saved_view.view_id == "view-e2e"
    assert saved_view.selected_evidence_ids == [EVIDENCE_IDS[0], EVIDENCE_IDS[1]]
    assert saved_view.rejected_evidence_ids == [EVIDENCE_IDS[2]]
    assert saved_view.rejected_evidence_reasons == {EVIDENCE_IDS[2]: "이번 범위 밖"}
    assert saved_view.counterarguments == ["이미 선반영되었을 수 있다."]
