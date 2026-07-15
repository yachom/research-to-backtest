# CLAUDE.md — Research-to-Backtest 세션 가이드

채용 과제 프로젝트: DART·XBRL·시장 데이터 기반 기업 리서치 → **사용자** 투자 가설 →
전략 DSL → Point-in-Time 백테스트. AI는 후보 정리·초안만 담당한다(HITL v2).

## 0. 세션 시작 시 읽는 순서

1. **docs/PROGRESS.md** — 최신 스냅샷(맨 위)이 현재 상태·다음 작업이다
2. docs/MILESTONES.md — 계획(Phase A/B/C·H)·결정 기록 D1~D9·정오표
3. docs/DATA_NOTES.md — 실측 관찰(설계를 바꾼 발견들). 새 마일스톤 착수 전 필독
4. 해당 마일스톤의 docs/specs/*.md — 구현 계약

정본 관계: `README.md` = 사용자 작성 기술 명세 v1.0(원본 보존, §1.1만 v2로 개정됨) /
`1804_FEEDBACK.md` = 요구 변경 v2(HITL) 원문 / `docs/HUMAN_IN_THE_LOOP.md`·
`AI_ROLE_BOUNDARY.md`·`OUTPUT_SCHEMA.md` = v2의 구현 관점 정리.

## 1. 진행 방식 (D8 — 반드시 준수)

- **메인 세션**: 설계·구현 명세(docs/specs/) 작성, 병합, 전체 품질 게이트, CLI 등 공유 파일 수정, 커밋.
- **구현·테스트**: 워크트리 격리 병렬 에이전트에 위임. 명세에 **파일 소유권**과 **인터페이스 계약**을 명시해 충돌을 설계 단계에서 차단. 에이전트는 자기 브랜치에 커밋 → 메인이 `git merge --no-ff`.
- **모델 정책: 하위 에이전트에 Fable 절대 금지** — `model: "opus"`(복잡 로직) 또는 `"sonnet"`(촘촘한 명세) 명시.
- 에이전트 워크트리 셋업 관례: 자체 `.venv` 생성, 실데이터는 `DATA_DIR=/Users/baemingyu/project/MC_investment_homework/data`, API 필요 시 메인 레포 `.env`를 `set -a && source ... && set +a`로 주입(키 값 출력 금지).

## 2. 품질 게이트·명령

```bash
make check          # ruff + format --check + mypy(strict) + pytest(unit)
# integration 포함 전체 (실 API + 실데이터):
set -a && source .env && set +a && DATA_DIR=$PWD/data .venv/bin/python -m pytest
```

- 현재 기준: **689 passed** / mypy strict 132파일 0 이슈. 병합 후 이 상태를 유지하지 못하면 병합하지 말 것.
- CLI: `.venv/bin/r2b` — 데이터: `resolve-company`·`collect-financials`(--include-xbrl 포함)·`collect-market`·`build-financials`·`parse-xbrl`·`reconcile-financials`. HITL(run 기반, 게이트 위반 exit 4): `create-run`·`runs`·`status`·`create-analyst-view`·`create-hypothesis`·`approve-strategy`·`backtest`·`submit-interpretation`. 상태 인지형 스텁(게이트 검사 후 exit 2): `generate-candidates`(C1')·`generate-strategy-draft`(C2')·`generate-report`(C3')·`research`(C1'). 계약: docs/specs/CLI-integration.md.

## 3. 절대 규칙

1. **Point-in-Time 원칙 훼손 금지** — 재무값은 `available_from`(접수일 다음 거래일, KRX 실캘린더) 이후에만. as-of join 외 병합 금지(README §22.3).
2. **승인 게이트 우회 금지** — 미승인 가설·전략은 실행 불가(`core/hitl/gates.py`). 테스트 플래그(`--auto-approve-for-test` 등)는 산출물에 auto_approved 기록 필수.
3. **AI/인간 저작 구분 유지** — AI 후보와 인간 가설은 다른 모델·다른 파일. content_origin 저장.
4. API 키·자격증명을 로그·예외·출력·커밋에 남기지 않는다. `data/`·`outputs/`·`.env`·`.claude/`는 커밋 금지(.gitignore 처리됨).
5. 실데이터가 명세와 다르면 **조용히 우회하지 말고** DATA_NOTES에 기록하고 명세를 고친다(지금까지 설계를 고친 실측: CIS 단일 손익, CF 누적 의미론, XBRL 차원 구분, KRX 로그인 의무화).

## 4. 환경·키 현황 (2026-07-15 기준)

- `.env`에 **DART_API_KEY·KRX_ID/KRX_PW 있음**.
- **LLM(D2 재개정)**: Claude Agent SDK + 별도 구독 계정의 `CLAUDE_CODE_OAUTH_TOKEN` — **.env에 발급 완료(2026-07-15), live 호출 가능 상태**. **ANTHROPIC_API_KEY와 동시 설정 금지**(API 키가 우선해 과금됨 — 현재 미설정 확인). 폴백 OpenRouter(키 없음). 제3자 서비스 배포 시에는 API 키 필수(정책). Phase C 착수 시 스모크 테스트: `claude-agent-sdk` 설치 → 1회 호출로 토큰·구독 인증 동작 확인부터.
- KIS·토스증권 키는 후순위(.env.example 참고).
- Python 3.14 venv(`.venv`), 의존성: pydantic·httpx·typer·pykrx·pandas·pyarrow (uv 미사용, pip).
- MVP 대상: SK하이닉스(corp_code 00164779, stock 000660), 12월 결산 가정.

## 5. 코드 규약

- 한국어 docstring + README·명세 § 참조, 식별자 영어. mypy strict·ruff(line 100) 통과 유지.
- 새 공용 예외는 `core/exceptions.py`(메인 세션만 수정). 날짜는 `datetime.date`, 시각은 KST.
- 커밋: 한국어 제목, 마지막 줄 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 레이아웃: `core/`(데이터 플랫폼+공용) · `research/`(P1) · `quant/`(P2) · `app/`(CLI). 매핑은 MILESTONES §4.

## 6. 다음 작업 (이 순서로)

1. ~~CLI 통합 패스~~ — **완료(2026-07-15)**: r2b 18명령 + 게이트 exit 4 + RunManifest. 계약은 docs/specs/CLI-integration.md, 스냅샷은 PROGRESS #3.
2. **Wave 3**: C1'(Evidence Store 구축 + CandidateAnalysis·HypothesisCandidate 생성기 + 프롬프트 버전 파일 + AIUsageRecord — `research`·`generate-candidates` 실구현) ∥ C2'(승인 가설 → DSL 초안 → StrategyReview — `generate-strategy-draft` 실구현) → C3'(15-섹션 보고서·Streamlit 7화면·강건성 분석·문서 재편 §25 — `generate-report` 실구현). 착수 시 LLM 스모크 테스트 먼저(§4).
3. 제출물 마감: 과제1·과제2 PDF, README 재편(실행 가이드) — C3'.
