"""프로젝트 공통 예외 계층."""

from research_backtest.core.constants import (
    DART_STATUS_MESSAGES,
    NO_DATA_DART_CODES,
    RETRYABLE_DART_CODES,
)


class ResearchBacktestError(Exception):
    """프로젝트 공통 최상위 예외."""


class ConfigError(ResearchBacktestError):
    """환경변수·설정 누락 또는 잘못된 설정."""


class DartApiError(ResearchBacktestError):
    """DART API가 정상(000) 이외의 상태 코드를 반환한 경우 (README §27)."""

    def __init__(self, status_code: str, message: str | None = None) -> None:
        self.status_code = status_code
        self.message = message or DART_STATUS_MESSAGES.get(status_code, "알 수 없는 상태 코드")
        super().__init__(f"DART API 오류 [{status_code}] {self.message}")

    @property
    def retryable(self) -> bool:
        return self.status_code in RETRYABLE_DART_CODES

    @property
    def is_no_data(self) -> bool:
        return self.status_code in NO_DATA_DART_CODES


class DartTransportError(ResearchBacktestError):
    """네트워크·HTTP 계층 오류로 재시도가 소진된 경우 (README §27.2).

    메시지에 포함되는 URL·오류 문자열은 인증키가 redact된 상태여야 한다(README §30.2).
    """


class DataValidationError(ResearchBacktestError):
    """수집·정규화 데이터가 검증 규칙(README §16)을 통과하지 못한 경우."""


class MarketAuthError(ResearchBacktestError):
    """KRX 로그인 자격증명(KRX_ID/KRX_PW)이 없어 수집할 수 없는 데이터셋 (MILESTONES D1 개정).

    KRX가 2025년부터 데이터 조회에 로그인을 의무화해 투자자 수급·지수는
    자격증명 없이는 수집이 불가능하다. 미로그인 실패가 "빈 DataFrame"으로
    나타나므로 호출 전에 이 예외로 차단한다(명세 A3 §0, §2).
    """


class CalendarRangeError(ResearchBacktestError):
    """거래일 캘린더 coverage 밖 날짜 조회 (명세 A3 §4).

    주말 로직 등으로 조용히 대체하면 룩어헤드·오정렬(README §4, §22)의
    원인이 되므로 즉시 실패한다.
    """


class LookaheadError(ResearchBacktestError):
    """Point-in-Time 원칙(README §4, §22) 위반: 기준일 이후 정보가 사용된 경우."""
