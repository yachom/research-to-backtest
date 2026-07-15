"""Streamlit 엔트리 (docs/specs/W3c-report-ui.md §3.1, S1 소유).

실행: ``streamlit run src/research_backtest/app/streamlit_app.py``.

사이드바(run 선택·생성·상태 배지) + 본문 7화면 탭 네비게이션으로 구성한다.
비즈니스 로직은 전혀 갖지 않는다 — :mod:`research_backtest.app.ui.state`\\ 로
잠금 여부를 읽고, :mod:`research_backtest.app.ui.screens`\\ 의 렌더 함수를
호출할 뿐이다. ``app/cli.py``·``app/commands/``는 import하지 않는다(§1).
"""

from __future__ import annotations

import streamlit as st  # streamlit 1.59는 py.typed로 인라인 타입을 배포한다(stub 부재 아님).
from pydantic import ValidationError as PydanticValidationError

from research_backtest.app.ui import screens, state
from research_backtest.core.config import get_settings
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.store import RunStore

st.set_page_config(page_title="Research-to-Backtest", layout="wide")

st.title("Research-to-Backtest — Human-in-the-Loop")
st.caption(
    "AI는 사실과 후보 관계를 정리하는 보조 도구다. 분석 관점·핵심 논지·근거 선택·"
    "투자 가설·전략 승인·결과 해석은 사용자가 담당한다(docs/HUMAN_IN_THE_LOOP.md)."
)

_settings = get_settings()
_selected_run_id = screens.render_sidebar(_settings)

if _selected_run_id is None:
    st.info("사이드바에서 run을 선택하거나, 아래 화면①에서 새 run을 생성하세요.")
    _tabs = st.tabs(list(state.SCREEN_TITLES))
    with _tabs[0]:
        screens.render_screen1(_settings, None)
    for _tab, _title in zip(_tabs[1:], state.SCREEN_TITLES[1:], strict=True):
        with _tab:
            st.subheader(_title)
            st.info("먼저 사이드바에서 run을 선택하거나 생성하세요.")
else:
    _store = RunStore(_settings.outputs_dir, _selected_run_id)
    try:
        _run_state = _store.load_run_state()
    except (DataValidationError, PydanticValidationError) as err:
        st.error(f"run_state를 읽을 수 없습니다: {err}")
    else:
        st.markdown(
            f"### 현재 상태: {state.PIPELINE_STATE_LABELS[_run_state.current_state]} "
            f"(run: `{_run_state.run_id}`)"
        )
        st.caption(f"다음 단계: {state.NEXT_STEP_HINTS[_run_state.current_state]}")

        _tabs = st.tabs(list(state.SCREEN_TITLES))
        with _tabs[0]:
            screens.render_screen1(_settings, _selected_run_id)
        with _tabs[1]:
            screens.render_screen2(_settings, _store, _run_state)
        with _tabs[2]:
            screens.render_screen3(_store, _run_state)
        with _tabs[3]:
            screens.render_screen4(_store, _run_state)
        with _tabs[4]:
            screens.render_screen5(_settings, _store, _run_state)
        with _tabs[5]:
            screens.render_screen6(_settings, _store, _run_state)
        with _tabs[6]:
            screens.render_screen7(_store, _run_state)
