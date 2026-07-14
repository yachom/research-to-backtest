# 실데이터 관찰 기록 (Data Notes)

수집된 실제 데이터에서 관찰된, 후속 마일스톤 설계에 영향을 주는 특성을 기록한다.
(근거 데이터: SK하이닉스 00164779, 2021~2025 사업연도, 전체 재무제표 API 40개 응답 6,436행 — A2 수집분)

## A2 수집분 → A4(계정 정규화·시계열) 설계 입력

1. **SK하이닉스는 손익계산서(IS)가 없다 — 손익이 전부 CIS(포괄손익계산서)에 있다.**
   40/40 파일 모두 sj_div=IS 행이 0개. 매출액·영업이익·당기순이익이 단일
   포괄손익계산서 체계로 CIS에 담긴다. → **registry의 손익 계정은 IS·CIS 모두
   허용해야 한다** (configs/account_registry.yaml의 statement_type 처리 주의).

2. **`account_id = "-표준계정코드 미사용-"` 행이 857/6,436 (13.3%).**
   유형자산의취득 등 CF 세부계정이 다수 포함. → account_id 단독 매칭은 불가능,
   **account_nm(label) 매칭을 반드시 병행** (README §12.1 우선순위 그대로).

3. **동일 account_id가 여러 재무제표에 등장한다.**
   `ifrs-full_ProfitLoss`가 SCE에 176행, `ifrs-full_Equity`가 SCE에 280행 등.
   SCE는 account_detail에 차원 정보(`연결재무제표 [member]`, `자본 [구성요소]|…`)를
   담는다. → **account_id 매칭 시 sj_div 필터 필수**, SCE는 A4 범위에서 제외하거나
   account_detail 차원 해석 후 사용.

4. **금액 필드 특성**: `thstrm_amount` 빈 행 125개(1.9%, CF 44·BS 15·SCE 66) —
   빈 값→None 처리 필요(0과 구분, README §9.6). `thstrm_add_amount`(분기 누적)
   10.2%, `frmtrm_q_amount` 49% 존재 — **단독분기 역산(README §11) 소스 확보 확인됨**.

5. **currency는 100% KRW. 파일당 rcept_no는 전부 1개** — 이 기간 SK하이닉스
   정기보고서에 정정 흔적 없음. Current View 한계(financial_api.py docstring)는
   B4에서 처리하되, MVP 데이터에서는 실질 차이 없음.

6. **2024 반기보고서부터 SCE 행수 급증**(약 50→120행, 당기·전기 2기간 표시).
   SCE를 쓰게 되면 기간 구분 주의.

## A3 시장 데이터(pykrx 1.2.8) 관찰 — 2026-07-14 실측

1. **KRX 로그인 의무화(2025~)**: 수정주가 OHLCV(`adjusted=True`, Naver 경유)만
   무로그인 동작. 투자자 수급·지수·원주가는 `KRX_ID`/`KRX_PW`(data.krx.co.kr
   무료 계정) 필요. **미로그인 실패가 예외가 아니라 빈 DataFrame으로 위장**되므로
   어댑터는 빈 결과를 오류로 취급한다.
2. **pykrx는 import 시점에 로그인을 시도**한다 → 자격증명 주입(os.environ) 후
   lazy import 필수 (core/market/source.py). 로그인 성공 시 pykrx 자체가
   stdout에 `로그인 ID: <값>`을 출력한다(pykrx 코드, 억제 불가 — 허용 소음).
3. 무로그인 OHLCV 실측: 000660 2015-01-02~2026-07-13 **2,829행**, 컬럼
   시가/고가/저가/종가/거래량(int64)+등락률(float64). volume=0 행 0개,
   high<low 위반 0건. 수정주가는 소급 수정 방식이며 거래량은 미수정일 수 있음
   (신호·체결 모두 수정주가 기준 — MVP 설계 결정, source.py docstring).
4. 투자자 순매수 원본 컬럼(소스 실측): 기관합계/기타법인/개인/외국인합계/전체
   (`on="순매수"` 기본) → foreign/institution_net_buy_value로 매핑.
5. 거래일 캘린더는 KOSPI 지수(1001) 거래일에서 구축 — 종목 거래정지에
   영향받지 않는다. coverage 밖 조회는 CalendarRangeError로 즉시 실패
   (주말 로직 대체 금지 — 룩어헤드 방지).

## B1+B2 XBRL 실측 (2026-07-14, SK하이닉스 2021~2025 정기보고서 22건)

1. **ZIP 구성**: instance **1개**(`entity{corp_code}_{결산일}.xbrl`) + `.xsd` + 링크베이스
   5종(`_cal`·`_def`·`_pre`·`_lab-ko`·`_lab-en`). 파일명에 결산일이 들어가 비고정 →
   루트 태그 `{xbrli}xbrl`로 판별(파일명 가정 금지 원칙 유효).
2. **연결·별도는 파일로 분리되지 않는다** — 단일 instance 안에서
   `ConsolidatedAndSeparateFinancialStatementsAxis`(Consolidated/SeparateMember)
   **차원**으로 구분된다. ⇒ **README §10.1의 "추가 Dimension이 없는 기본 Context"
   규칙은 실데이터에 존재하지 않는다**(차원 0 context는 1개뿐, Assets 아님).
   B3의 Context 선택 규칙은 "연결/별도 축 **하나만** 있는 context에서 scope에 맞는
   member 선택"으로 수정해야 한다.
3. entity identifier scheme=`http://dart.fss.or.kr/ifrs/CIK`, 값=corp_code.
   차원은 전부 segment(scenario 미사용). nil fact 0건, decimals에 `INF` 존재.
4. 규모: 사업보고서당 fact 7,000~8,000(ifrs-full 5,000~6,300 + 기업 확장
   entity00164779 ~1,000 + dart ~600~830 + dart-gcd 114), context ~2,400~2,900.
5. **2020.12 사업보고서는 원본(20210322000782)과 [기재정정](20210330000776) 두
   접수번호가 모두 수집됨** — B4 정정공시 버전 그래프의 실데이터 케이스.

## 기업 식별(A1) 관찰

- 축약 검색어가 동명의 비상장사에 정확 일치할 수 있다 — 예: "삼성" →
  비상장 "삼성"(00893765)에 EXACT_NAME 매칭. 후보 테이블 + 종목코드 안내로
  커버 중. alias 테이블은 후순위.
- 고유번호 파일 규모: 118,484개사 (2026-07-14 기준).
