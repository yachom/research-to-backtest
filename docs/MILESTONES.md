# 개발 마일스톤 v1.1 — 수직 슬라이스 재구성

- 원본 계획: `README.md` §31 (v1.0). **명세 자체는 README를 계속 원본으로 유지**하며, 이 문서는 실행 순서와 결정 사항만 재구성한다.
- 각 마일스톤의 상세 스키마·완료 조건은 README 해당 절을 따른다.

## 1. 왜 순서를 바꾸는가

v1.0은 M0→M10 직렬(깊이 우선) 구성이라 **M9에 도달해야 처음으로 백테스트가 돈다.**
채용 과제 특성상 가장 큰 리스크는 "XBRL 파서는 훌륭한데 제출물(보고서+백테스트)이 없는 상태"다.

1. **일정 불확실성이 가장 큰 구간이 XBRL(M3~M5)이다.** 기업 확장계정, 제출 파일 구성 편차, dimension 처리 등 예측 불가능한 비용이 몰려 있다.
2. **시장 데이터(가격·수급·벤치마크)를 다루는 마일스톤이 없다.** M9 안에 암묵적으로 포함되어 있으나 실제로는 독립 작업량이다 — 수정주가 처리, KRX 거래일 캘린더(available_from 계산의 전제), 투자자 수급 수집.
3. **전체 재무제표 API(M2)만으로도 백테스트에 필요한 핵심 계정은 확보된다.** XBRL은 "검증·보존·세부계정" 계층으로 뒤에 붙여도 아키텍처가 성립한다 — README §5.1의 계층 설계 그대로다.

따라서 **Phase A에서 얇게 끝까지 관통**시키고, Phase B(XBRL 깊이)와 Phase C(AI 리서치)를 그 위에 쌓는다. 어느 시점에 멈춰도 제출 가능한 상태를 유지한다.

## 2. Phase 구성

### Phase A — 관통 (엔드투엔드 스켈레톤)

| ID | 원 마일스톤 | 내용 | 완료 조건 요약 |
|---|---|---|---|
| A0 | M0 | 프로젝트 기반 구축 | pytest·CLI help·lint·typecheck 통과 — ✅ 완료 (2026-07-14) |
| A1 | M1 | DART 기업·공시 식별 | `resolve-company`로 corp_code·최근 보고서 접수번호 출력 — ✅ 완료 (2026-07-14) |
| A2 | M2 | 전체 재무제표 API 수집 | 5개년 CFS·OFS raw 저장, 캐시·오류코드 처리 |
| A3 | (신설) | 시장 데이터 | pykrx로 수정주가 OHLCV·투자자 수급·KOSPI 수집, KRX 거래일 캘린더 |
| A4 | M6 축소 | 핵심 계정 정규화·시계열 | registry 기반 11개 계정, 단독분기 역산, YoY, available_from 부여 |
| A5 | M8 축소 | 전략 DSL 스키마·컴파일러 | README §23 기본 전략을 JSON으로 검증·컴파일 (LLM 없이) |
| A6 | M9 | 백테스트 엔진 | 룩어헤드 테스트(§28.3) 통과, 성과지표, Buy & Hold 비교 |

**Phase A 종료 시점 상태**: §23 기본 전략(실적 YoY + 외인 수급 + 60일 돌파)이 실데이터로 end-to-end 실행된다. 이 시점부터는 언제 멈춰도 "동작하는 백테스트 시스템"을 제출할 수 있다.

### Phase B — 깊이 (데이터 신뢰성 계층)

| ID | 원 마일스톤 | 내용 |
|---|---|---|
| B1 | M3 | XBRL 원본 수집·보존 (manifest, checksum, 오류 XML 탐지) |
| B2 | M4 | XBRL Fact·Context·Unit·Dimension 파싱 |
| B3 | M5 | 계정 표준화 고도화 + API-XBRL 정합성 검증(§16.4) |
| B4 | §15 | 정정공시 버전 관리 · Point-in-Time View 정식화 |

### Phase C — AI 리서치 및 마무리

| ID | 원 마일스톤 | 내용 |
|---|---|---|
| C1 | M7 | Evidence 생성 + LLM 기업분석·투자 가설 (structured output, LLM 호출 트레이스 기록 — 과제2 "AI 활용 검증자료"의 증빙) |
| C2 | M8 완성 | 자연어 아이디어 → DSL 변환 (LLM) + 미지원 변수 처리 |
| C3 | M10 | 강건성 분석 · Streamlit · 과제 보고서 · README 재편 |

- B와 C는 Phase A 완료 후 **병렬 진행 가능**하다.
- 일정이 부족하면 B2~B3를 "대표 계정 7개 교차검증"(§16.4 최소선)으로 축소한다.
- XBRL 파싱 범위는 MVP 기업(SK하이닉스)의 정기보고서로 한정한다. 전 기업 일반화는 후순위(§32).

## 3. 결정 기록 (Decision Log)

| # | 결정 | 근거 | 되돌리려면 |
|---|---|---|---|
| D1 | 가격·수급·지수 데이터는 **pykrx** | 무료·API 키 불필요·투자자 수급 제공. KIS API는 후순위 | A3의 data_sources 어댑터만 교체 |
| D2 | LLM은 **OpenRouter 무료 모델** `inclusionai/ling-2.6-flash:free` (`OPENROUTER_API_KEY`, OpenAI 호환 API) | 비용 0 우선. 클라이언트는 provider 중립(OpenAI 호환)으로 설계해 모델 교체는 `LLM_MODEL` 설정 1줄. 경량 모델 전제 보완책: ① JSON Schema 검증+재시도 루프 ② Evidence 사전계산으로 프롬프트 최소화 ③ 섹션별 분할 호출 ④ 무료 티어 rate limit 대응(호출 수 최소화·캐시). 프롬프트는 버전 관리되는 파일로 저장 | `LLM_MODEL` 환경변수 |
| D3 | 패키지는 `src/research_backtest/` + 콘솔 스크립트 **`r2b`** | §26의 `python -m src.app.cli`는 `src`를 패키지명으로 쓰는 비표준 구조. 내부 서브패키지 구조(§25)는 그대로 유지 | pyproject 스크립트 항목 |
| D4 | `include_news` 기본값 **False** | §3.2(True)와 §32(MVP에 뉴스 없음)의 모순 해소 | `common/models.py` 한 줄 |
| D5 | MVP 대상 기업 **SK하이닉스(000660)** | README 예시 전반과 일치 | 실행 인자일 뿐, 언제든 변경 가능 |
| D6 | `data/`·`outputs/`는 커밋하지 않고 런타임 생성 | 원본 데이터·키 커밋 방지(§30 취지) | .gitignore |
| D7 | 레포는 **단일 패키지 + 3분할 서브패키지**: `core`(공용 데이터 플랫폼) / `research`(Project 1) / `quant`(Project 2). 공용 configs·.env는 루트 | P1·P2 모두 시장 데이터와 재무 시계열을 사용하므로 순수 2분할은 ETL 중복 또는 교차 import를 유발. 설치·재현은 pyproject 1개(`make install` 한 번)로 유지. P1→P2 계약은 코드가 아닌 **산출물**(hypothesis JSON + core가 발행한 PIT 데이터셋) | uv workspace로 물리 분리(필요 시) |
| D8 | **진행 방식**: 메인 세션은 설계·구현 명세(`docs/specs/`) 담당, 세부 코드 구현·테스트는 하위 에이전트 세션에 위임. 명세 문서가 구현의 계약이며, 메인 세션이 결과를 검증(lint·type·test·실동작) | 명세 없이 구현된 코드는 리뷰에서 반려 | 세션 운영 방식이므로 코드 영향 없음 |

## 4. 레포 레이아웃 v2 (D7)

```text
MC_investment_homework/
├── README.md            # 명세 원본 (v1.0, 사용자 작성)
├── pyproject.toml       # 설치 단위는 하나
├── .env / .env.example  # 공용 환경변수 (루트)
├── configs/             # 공용 설정 (루트)
├── docs/
│   ├── MILESTONES.md    # 실행 계획·결정 기록 (이 문서)
│   └── specs/           # 마일스톤별 구현 명세 (구현의 계약, D8)
├── src/research_backtest/
│   ├── core/            # 공용: 모델·설정·달력·예외 + DART·시장 데이터 ETL + 재무 정규화
│   ├── research/        # Project 1: Evidence → LLM 분석 → 리포트·투자 가설
│   ├── quant/           # Project 2: 전략 DSL → 백테스트 → 강건성
│   └── app/             # 통합 CLI (r2b)
└── tests/               # unit(오프라인) / integration(실 API, 키 없으면 skip)
```

README §25와의 매핑: `common`·`data_sources`·`xbrl`·`financials` → `core`,
`disclosures`·`research` → `research`(P1), `strategy`·`backtest` → `quant`(P2).

## 5. README(v1.0) 정오표 — 다음 개정 시 반영

1. **§21.2 ↔ §23.4 불일치**: 전략 예시가 `rolling_high_60_lag1`을 사용하나 허용 가격지표 목록에는 `rolling_high_60`만 있다. → DSL에 `lag(indicator, n)`을 정식 도입하거나 lagged 지표를 목록에 등록.
2. **§3.2 ↔ §32 모순**: `include_news: True` 기본값 vs MVP 범위에 뉴스 미포함. → 기본 False (D4 반영 완료).
3. **§26 CLI 형태**: `python -m src.app.cli …` → 콘솔 스크립트 `r2b …` (D3 반영 완료).
4. **§31 마일스톤 공백**: 시장 데이터 수집이 어느 마일스톤에도 없음 → A3로 신설.
5. **데이터 경계**: 전체 재무제표 API는 2015년 이후만 제공(§6.4). 백테스트를 2016-01-01에 시작하면 2015년 연간·분기 데이터가 선행 재무로 필요 — 경계 검증 필요. 2015 이전으로 확장하려면 별도 소스가 필요하다.
6. **문서 재편(§25·DoD 20)**: 최종 제출 시 README는 실행 방법·설계 요약으로 재편하고, 현재 명세 전문은 `docs/PROJECT_SPEC.md`로 이동 — C3에서 수행.
