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

## 기업 식별(A1) 관찰

- 축약 검색어가 동명의 비상장사에 정확 일치할 수 있다 — 예: "삼성" →
  비상장 "삼성"(00893765)에 EXACT_NAME 매칭. 후보 테이블 + 종목코드 안내로
  커버 중. alias 테이블은 후순위.
- 고유번호 파일 규모: 118,484개사 (2026-07-14 기준).
