from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Final
from zoneinfo import ZoneInfo

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.timezones import valid_timezone
from app.models.kb_chunk import EMBEDDING_DIMENSIONS

# HS256 keys shorter than the hash output weaken the signature.
JWT_SECRET_MIN_BYTES: Final = 32


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EmailBackend(StrEnum):
    # Plain SMTP, for Mailpit locally.
    SMTP = "smtp"
    # Amazon SES through its API, with the instance role's credentials.
    SES = "ses"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # A validation error otherwise prints the input it rejected, which for a missing
        # field is every other value read from the environment, keys included.
        hide_input_in_errors=True,
    )

    environment: Environment = Environment.LOCAL
    log_level: LogLevel = LogLevel.INFO

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://gpureserve:gpureserve@localhost:5432/gpureserve"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")
    # Availability and slot search read GPU allocations through Redis. Off sends every
    # read to Postgres (used to measure the difference).
    availability_cache_enabled: bool = True
    # A backstop only: commits invalidate entries. It bounds how long an entry can be
    # stale if an invalidation is lost.
    availability_cache_ttl_seconds: int = Field(default=300, gt=0)
    # Count cache hits and misses in Redis. For the load test's hit-rate run only: it
    # adds a Redis round trip to every read.
    availability_cache_stats: bool = False

    # Comma-separated in the environment, e.g. "http://localhost:5173,https://app.example.com".
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # How long a request waits for a database connection when all are in use. Past it,
    # the request gets 503 with Retry-After instead of waiting on.
    database_pool_timeout_seconds: float = Field(default=3.0, gt=0)
    database_busy_retry_after_seconds: int = Field(default=2, gt=0)

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

    # Outgoing email. Locally, Mailpit accepts SMTP on port 1025.
    email_backend: EmailBackend = EmailBackend.SMTP
    email_from: str = "GPU Reserve <no-reply@gpureserve.dev>"
    # SES only. Unset means the default from the environment or instance metadata.
    ses_region: str | None = None
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, gt=0)
    smtp_timeout_seconds: float = Field(default=10.0, gt=0)
    # Links in emails point here.
    frontend_url: str = "http://localhost:5173"

    # Presigned upload URLs per user, so one account cannot fill the bucket.
    presign_user_limit: int = Field(default=30, gt=0)
    presign_user_window_seconds: int = Field(default=10 * 60, gt=0)

    # Weekly GPU-hour quotas run Monday 00:00 to Monday 00:00 in this one zone, for
    # every lab. It is set on its own, not taken from the lab default, so changing the
    # default for new labs can never move existing quota weeks.
    quota_timezone: str = "America/Los_Angeles"

    # Knowledge base. The default is the repository's kb/ directory when running on the
    # host; the image sets KB_DIR to the copy inside it.
    kb_dir: Path = Path(__file__).resolve().parents[3] / "kb"
    # Unset means no embeddings: ingestion refuses to run, and so will policy search.
    openai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    # Must equal the width of kb_chunks.embedding; requested from the API explicitly.
    embedding_dimensions: int = EMBEDDING_DIMENSIONS
    # Sections returned per policy search.
    rag_top_k: int = Field(default=5, gt=0)
    # Minimum cosine similarity for a result. Measured for one embedding model: a new
    # model means re-recording the evaluation embeddings and measuring it again.
    # No cut by default: on the dev set no threshold separates in-scope questions from
    # out-of-scope ones (evals/rag/results.md), so out-of-scope handling is the prompt's job.
    rag_min_score: float = Field(default=0.0, ge=-1.0, le=1.0)

    # Assistant. "<provider>:<model>": openai (OPENAI_API_KEY), anthropic
    # (ANTHROPIC_API_KEY), or vllm (VLLM_BASE_URL and VLLM_API_KEY). The scripted provider
    # drives end-to-end tests and is refused outside ENVIRONMENT=test.
    assistant_model: str = "openai:gpt-5.4-mini-2026-03-17"
    anthropic_api_key: SecretStr | None = None
    # An OpenAI-compatible vLLM server, e.g. https://<host>/v1. Its served model name must
    # equal the model part of ASSISTANT_MODEL.
    vllm_base_url: str | None = None
    vllm_api_key: SecretStr | None = None
    # Thinking on or off for models whose chat template has the switch (Qwen3). Unset
    # sends nothing, so the template's default applies.
    vllm_enable_thinking: bool | None = None
    # Per model request; a run makes several. The whole run is capped separately.
    assistant_request_timeout_seconds: float = Field(default=30.0, gt=0)
    assistant_request_retries: int = Field(default=1, ge=0)
    assistant_run_timeout_seconds: float = Field(default=300.0, gt=0)
    # Messages and resumes per user, in fixed windows.
    assistant_minute_limit: int = Field(default=20, gt=0)
    assistant_day_limit: int = Field(default=200, gt=0)
    # Assistant messages from all users together per UTC day. Per-user limits do not stop
    # many new accounts from adding up; this bounds the model bill. Confirmations of an
    # action the assistant already proposed are not counted, so they always go through.
    assistant_global_day_limit: int = Field(default=300, gt=0)

    # A public demo account, shown on the sign-in page. Both set, or neither. The
    # account cannot change its name, avatar, or password, and failed sign-ins do not
    # lock it, so visitors cannot take it away from each other.
    demo_login_email: str | None = None
    demo_login_password: SecretStr | None = None

    # Notifications to addresses in these domains are skipped instead of sent: the demo
    # accounts use example.edu, which cannot receive mail (and SES's sandbox refuses it).
    email_suppressed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Langfuse tracing; skipped unless both keys are set.
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None

    @property
    def tracing_enabled(self) -> bool:
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None

    @field_validator(
        "s3_endpoint_url",
        "s3_public_endpoint_url",
        "s3_region",
        "s3_access_key_id",
        "s3_secret_access_key",
        "ses_region",
        "openai_api_key",
        "anthropic_api_key",
        "vllm_base_url",
        "vllm_api_key",
        "vllm_enable_thinking",
        "langfuse_public_key",
        "langfuse_secret_key",
        "langfuse_host",
        "demo_login_email",
        "demo_login_password",
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

    @field_validator("log_level", mode="before")
    @classmethod
    def log_level_upper(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("email_suppressed_domains", mode="before")
    @classmethod
    def split_domains(cls, value: object) -> object:
        if isinstance(value, str):
            return [domain.strip().lower() for domain in value.split(",") if domain.strip()]
        return value

    @field_validator("demo_login_email")
    @classmethod
    def demo_email_normalized(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None

    @model_validator(mode="after")
    def demo_login_complete(self) -> "Settings":
        if (self.demo_login_email is None) != (self.demo_login_password is None):
            raise ValueError("set both DEMO_LOGIN_EMAIL and DEMO_LOGIN_PASSWORD, or neither")
        return self

    def is_demo_account(self, email: str) -> bool:
        return self.demo_login_email is not None and email == self.demo_login_email

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

    @field_validator("embedding_dimensions")
    @classmethod
    def embedding_dimensions_match_column(cls, value: int) -> int:
        if value != EMBEDDING_DIMENSIONS:
            raise ValueError(f"must be {EMBEDDING_DIMENSIONS}, the width of the embedding column")
        return value

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
