from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final
from zoneinfo import ZoneInfo

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.timezones import valid_timezone

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
    # Wrong current passwords on a password change, per user.
    password_change_failure_limit: int = Field(default=5, gt=0)
    password_change_window_seconds: int = Field(default=15 * 60, gt=0)

    # Object storage. Without an endpoint URL, boto3 talks to AWS S3 and takes
    # credentials from the environment or the instance role.
    s3_endpoint_url: str | None = None
    # The endpoint browsers use. Presigned URLs are signed for this host, so it must be
    # the address the browser sends the upload to. Defaults to s3_endpoint_url.
    s3_public_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_bucket_uploads: str = "gpu-reserve-uploads"
    # How long a presigned upload stays usable.
    upload_url_ttl_seconds: int = Field(default=300, gt=0)

    # Weekly GPU-hour quotas run Monday 00:00 to Monday 00:00 in this one zone, for
    # every lab. It is set on its own, not taken from the lab default, so changing the
    # default for new labs can never move existing quota weeks.
    quota_timezone: str = "America/Los_Angeles"

    @field_validator(
        "s3_endpoint_url",
        "s3_public_endpoint_url",
        "s3_region",
        "s3_access_key_id",
        "s3_secret_access_key",
        mode="before",
    )
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        # An env file line like `S3_ACCESS_KEY_ID=` means "not set". Passing "" to boto3
        # would count as an explicit (empty) key and skip the default credential chain,
        # which is how production gets the instance role's credentials.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("quota_timezone")
    @classmethod
    def quota_timezone_supported(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("jwt_secret")
    @classmethod
    def jwt_secret_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode()) < JWT_SECRET_MIN_BYTES:
            raise ValueError(f"must be at least {JWT_SECRET_MIN_BYTES} bytes")
        return value

    @property
    def quota_zone(self) -> ZoneInfo:
        return ZoneInfo(self.quota_timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
