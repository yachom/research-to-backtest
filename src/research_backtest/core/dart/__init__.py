"""DART 연동 계층 — 클라이언트·고유번호·공시검색 (README §6, §19.1~19.2, Milestone A1).

- client: 인증·재시도·오류 매핑을 담당하는 HTTP 클라이언트
- corp_code: 고유번호 파일 수집·캐시와 기업 resolve
- disclosure_search: 정기보고서 검색·분류
- models: DartFiling·ResolveResult 등 식별 계층 도메인 모델
"""
