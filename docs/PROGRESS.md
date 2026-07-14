# 진행 기록 (Progress Log)

웨이브 경계마다 스냅샷을 추가한다(최신이 위). 결정의 근거는 MILESTONES.md,
실데이터 관찰은 DATA_NOTES.md, 요구 변경 원문은 1804_FEEDBACK.md 참고.

---

## 중간 기록 #2 — 2026-07-14 (Wave 2 완료)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | Wave 1 전체(A0~A5·B1+B2·H1) + **Wave 2 전체: A6 백테스트 엔진 · B3 API-XBRL 대조 · B4 정정공시 PIT** |
| ⏭ 남은 작업 | CLI 통합 패스(메인 세션 — build-financials·parse-xbrl·reconcile-financials·backtest + HITL 명령 8종) → Wave 3: C1' Evidence+후보 생성 · C2' 전략 초안+리뷰 · C3' 보고서·Streamlit·강건성 |

### 품질 게이트 (main)

pytest **623 passed**(전 integration 포함: 실 API + DATA_DIR 실데이터 백테스트), ruff·format 클린, **mypy strict 124파일 0 이슈**. 승인 게이트가 실행 경로에서 강제됨(미승인 → ApprovalGateError·산출물 미생성, 테스트 고정).

### Wave 2 하이라이트

1. **첫 실데이터 백테스트** (§23 기본 전략, 000660, 비용 반영):
   거래 5건(전부 2024~2025) · 누적 **+110.8%** · MDD −16.9% · 승률 60% · PF 8.13 ·
   노출률 5~10% · 2021~2025 KOSPI 대비 **+67.6%p** (2016~2025 전체로는 −8.9%p, 자산 B&H ~20배에는 크게 미달 — 노출률 관점 분리 필요).
   손절 갭 리스크 실증(-10% 기준, 실현 -12.9%) 등 관찰 5건 → DATA_NOTES.
2. **API-XBRL 대조 100%**: 연간 5개년×연결/별도×7계정 70건 전량 MATCH. README §34 서사의 실증.
3. **PIT 버전 관리 실데이터 재현**: 2020.12 원본→기재정정 체인, as_of별 가시 버전 경계값 정확.
4. 룩어헤드 방어 3중화: as-of join 방어선(LookaheadError) + §28.3 테스트 3종 + 절단 불변 property.

### 에이전트 운영 (전부 워크트리 격리 → 메인 병합·재검증)

A6=Opus(+2,550줄) · B3=Opus(+1,958줄) · B4=Sonnet(+721줄) — 3트랙 병렬, 명세 이탈은 전부 사유 기록 후 수용(예: A6 워밍업 보존은 절단 불변 property의 전제).

### 다음

CLI 통합 패스(메인 세션) 후 Wave 3. LLM(OpenRouter) 키는 여전히 미확보 — C1'·C2'는 fake 클라이언트+프롬프트 파일로 선구현, 키 확보 시 live 전환.

---

## 중간 기록 #1 — 2026-07-14 (Wave 1 완료 직전, Wave 2 착수 전)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | A0 기반 구축 · A1 기업식별 · A2 재무API 수집 · A3 시장데이터(캘린더 포함) · **A4 재무 정규화** · **A5 전략 DSL** · **B1+B2 XBRL 수집·파싱** |
| 🔄 실행 중 | **H1 HITL 기반 계층** (Sonnet, 워크트리) — 병합 대기 |
| ⏭ Wave 2 예정 | A6 백테스트 엔진(승인 게이트 내장) ∥ B3 API-XBRL 교차검증 ∥ B4 정정공시 PIT |
| ⏭ Wave 3 예정 | C1' Evidence+후보 생성 · C2' 전략 초안+리뷰 · C3' 보고서·Streamlit (HITL v2 흐름) |

### 품질 게이트 (main 기준)

- pytest **376 passed**(실 API integration 포함), ruff + ruff format clean, **mypy strict** 81개 파일 0 이슈
- 룩어헤드 방어 자산: available_from(접수일 다음 거래일, KRX 실캘린더) 550개 fact 전수 검증, 지표 레벨 no-lookahead property 테스트, 캘린더 coverage 밖 즉시 예외

### 데이터 자산 (data/, 전부 재현 가능·미커밋)

| 자산 | 규모 |
|---|---|
| DART 고유번호 | 118,484개사 (캐시) |
| 전체 재무제표 API raw | 40개 응답·6,436행 (2021~2025, CFS·OFS, sha256 보존) |
| 시장 데이터 | OHLCV·수급·KOSPI·거래일 캘린더 각 2,829행 (2015-01-02~2026-07-13) |
| XBRL 원본 | 22건(기재정정 1건 포함)·230MB, manifest·checksum 보존 |
| normalized 재무 | facts 550 · quarterly 40 · annual 10 · **metrics 136**(YoY 등, available_from 부여) |

### 이번 구간의 중요 사건

1. **요구사항 v2 (Human-in-the-Loop) 전환** — 원문 1804_FEEDBACK.md. AI 단독
   분석·가설 확정 구조 폐기, 사용자 관점·가설·승인·해석 중심으로 재설계(D9).
   기존 코드 충돌 0건(해당 영역이 미구현이라 처음부터 HITL로 설계).
   문서 3종 신설(HUMAN_IN_THE_LOOP·AI_ROLE_BOUNDARY·OUTPUT_SCHEMA) + README §1.1 개정.
2. **KRX 로그인 의무화 발견·대응** (D1 개정) — 수급·지수는 KRX_ID/PW 필요,
   어댑터가 부분 수집 모드 지원.
3. **실측이 설계를 세 번 고쳤다**: ① SK하이닉스 손익은 전부 CIS(→registry
   statement_types 복수화) ② 분기 CF는 thstrm이 누적(손익과 정반대 → 차분 파생)
   ③ XBRL 연결·별도는 파일이 아닌 차원 구분(→README §10.1 규칙 수정 필요, B3 반영 예정).

### 병렬 에이전트 운영 기록 (D8)

| 트랙 | 모델 | 산출 규모 | 검증(각 워크트리 → main 병합 후) |
|---|---|---|---|
| A1~A3 (순차기) | 정책 수립 전(메인 모델 상속) | dart·market 계층 | 각 마일스톤 DoD + 메인 세션 재검증 |
| A4 재무 정규화 | Opus | +2,905줄/17파일 | 211p(워크트리) → main 376p |
| A5 전략 DSL | Sonnet | +1,894줄/12파일 | 252p(워크트리) → main 260p |
| B1+B2 XBRL | Opus | +2,409줄/15파일 | 211p(워크트리) → main 314p |
| H1 HITL 기반 | Sonnet | 실행 중 | — |

방식: 메인 세션(Fable)이 명세(docs/specs/)·병합·품질 게이트·커밋 담당,
에이전트는 자기 소유 파일만 수정하고 워크트리 브랜치에 커밋(하위 에이전트 Fable 금지 — D8).

### 리스크·보류 사항

- **OpenRouter 키 미확보** — Phase C의 live LLM 호출 차단 요소(구조는 fake 클라이언트로 선구현 가능). 모델은 `inclusionai/ling-2.6-flash:free` 예정(D2).
- H1 병합 전 — Wave 2의 A6는 H1의 승인 게이트 API에 의존(명세 계약으로 선정의됨).
- 토스증권 API(.env.example의 CLIENT_ID/SECRET)는 전략 시행 테스트용 후순위로 등록만 됨.
- CLI의 HITL 명령 8종(generate-candidates ~ generate-report)은 H1 병합 후 메인 세션이 연결.

### 다음 (Wave 2)

- **A6**: A4 metrics as-of join + A5 컴파일 계약 + H1 게이트(승인 전략만 실행) + 다음날 시가 체결·비용·성과지표 + 룩어헤드 테스트(§28.3)
- **B3**: API-XBRL 대표 계정 교차검증 — Context 선택 규칙은 실측 반영("연결/별도 축 하나만 있는 context")
- **B4**: 정정공시 버전 그래프 — 실데이터 케이스(2020.12 원본+기재정정 쌍) 확보됨
