"""환경변수·설정 파일 기반 설정 (README §30, §6, §27)."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from research_backtest.core.exceptions import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dart_api_key: str = ""

    # LLM은 OpenRouter 경유 무료 모델 사용 (docs/MILESTONES.md D2). Phase C에서만 필요.
    openrouter_api_key: str = ""
    llm_model: str = "inclusionai/ling-2.6-flash:free"
    llm_base_url: str = "https://openrouter.ai/api/v1"

    data_dir: Path = Path("data")
    outputs_dir: Path = Path("outputs")
    log_level: str = "INFO"

    def require_dart_api_key(self) -> str:
        """DART API 사용 시점에 키 존재를 강제한다 — 미설정이면 즉시 실패."""
        if not self.dart_api_key:
            raise ConfigError(
                "DART_API_KEY가 설정되지 않았습니다. "
                ".env.example을 복사해 .env를 만들고 키를 입력하세요."
            )
        return self.dart_api_key


def get_settings() -> Settings:
    return Settings()


class DartRetryConfig(BaseModel):
    """DART API 재시도 정책 (README §27.3).

    max_attempts는 최초 시도를 제외한 최대 재시도 횟수이며, i번째 재시도 전에
    backoff_seconds[i]초 대기한다(목록을 넘어서면 마지막 값 유지).
    """

    max_attempts: int = Field(default=4, ge=0)
    backoff_seconds: list[float] = Field(default_factory=lambda: [1.0, 2.0, 4.0, 8.0])


class DartCorpCodeCacheConfig(BaseModel):
    """고유번호 파일 캐시 갱신 주기 (README §6.1)."""

    refresh_days: int = Field(default=7, ge=0)


class DartConfig(BaseModel):
    """configs/dart.yaml의 요청·캐시 설정 (README §6, §27).

    min_interval_seconds는 수집기의 **실제 API 호출 사이** 최소 대기
    간격이다 — 캐시 히트는 대기하지 않는다(명세 A2 §4).
    """

    timeout_seconds: float = Field(default=30.0, gt=0)
    min_interval_seconds: float = Field(default=0.1, ge=0)
    retry: DartRetryConfig = Field(default_factory=DartRetryConfig)
    corp_code_cache: DartCorpCodeCacheConfig = Field(default_factory=DartCorpCodeCacheConfig)


def load_dart_config(path: Path = Path("configs/dart.yaml")) -> DartConfig:
    """configs/dart.yaml을 읽어 DartConfig로 검증한다.

    base_url은 core.constants.DART_BASE_URL을 사용하므로 여기서는 읽지 않는다.
    """
    if not path.exists():
        raise ConfigError(f"DART 설정 파일이 없습니다: {path} (레포 루트에서 실행했는지 확인)")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"DART 설정 파일 형식이 잘못되었습니다(매핑이 아님): {path}")
    request: Any = raw.get("request") or {}
    if not isinstance(request, dict):
        raise ConfigError(f"DART 설정의 request 항목이 매핑이 아닙니다: {path}")
    try:
        return DartConfig.model_validate(
            {
                "timeout_seconds": request.get("timeout_seconds", 30.0),
                "min_interval_seconds": request.get("min_interval_seconds", 0.1),
                "retry": request.get("retry") or {},
                "corp_code_cache": raw.get("corp_code_cache") or {},
            }
        )
    except ValidationError as err:
        raise ConfigError(f"DART 설정 값이 잘못되었습니다: {err}") from err
