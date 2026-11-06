from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# HS256 keys shorter than the hash output weaken the signature.
JWT_SECRET_MIN_BYTES: Final = 32


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://gpureserve:gpureserve@localhost:5432/gpureserve"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Comma-separated in the environment, e.g. "http://localhost:5173,https://app.example.com".
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # Seconds to wait on each dependency in the readiness probe.
    ready_check_timeout_seconds: float = 2.0

    # Prefix for the `type` URI of problem-details error responses.
    problem_base_url: str = "https://gpureserve.dev/errors"

    # Signing key for access tokens. Required, with no default: every environment
    # supplies its own value (Compose and CI set a development-only one).
    jwt_secret: SecretStr
    access_token_ttl_minutes: int = Field(default=15, gt=0)
    refresh_token_ttl_days: int = Field(default=7, gt=0)
    # Browsers only send Secure cookies over HTTPS (Chrome and Firefox also allow
    # http://localhost). Turned off for plain-HTTP local setups.
    refresh_cookie_secure: bool = True

    # Login and registration throttling (fixed windows in Redis). Every login attempt
    # counts against the client IP; only failed attempts count against the email.
    login_ip_limit: int = Field(default=60, gt=0)
    login_ip_window_seconds: int = Field(default=60, gt=0)
    login_email_failure_limit: int = Field(default=5, gt=0)
    login_email_window_seconds: int = Field(default=15 * 60, gt=0)
    register_ip_limit: int = Field(default=20, gt=0)
    register_ip_window_seconds: int = Field(default=60 * 60, gt=0)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < JWT_SECRET_MIN_BYTES:
            raise ValueError(f"must be at least {JWT_SECRET_MIN_BYTES} bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
