# 제출물 (Submission)

| 파일 | 내용 |
|---|---|
| `과제1_기업분석보고서.md` / `.pdf` | SK하이닉스 분석 보고서 — 파이프라인이 생성한 15-섹션 보고서(섹션별 저작 주체 태그 포함) |
| `과제2_AI활용검증.md` / `.pdf` | AI를 어디에 어떻게 썼고 무엇으로 통제·증빙했는가 |
| `evidence/ai_usage_log.jsonl` | 위 보고서를 만든 실제 LLM 호출 기록 4건 (stage·model·prompt 버전·입출력 산출물) |
| `evidence/run_state.json` | 파이프라인 상태 전이 이력 — 승인 게이트 통과 기록 (actor·시각·auto_approved) |
| `evidence/robustness_report.json` | 강건성 분석 원자료 (조건 제거 5변형·비용 민감도·하위 기간) |

생성 run: `20260715_152048_SK_HYNIX_INC` (분석 기준일 2025-12-31).
시스템 전체 구현 정리는 `docs/SOLUTION_OVERVIEW.md`, 실행 방법은 레포 루트 `README.md`.
