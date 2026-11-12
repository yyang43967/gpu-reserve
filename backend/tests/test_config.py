"""Settings validation that does not need any service running."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_quota_timezone_defaults_independently_of_the_lab_default() -> None:
    settings = Settings()

    assert settings.quota_timezone == "America/Los_Angeles"
    assert settings.quota_zone.key == "America/Los_Angeles"


def test_quota_timezone_can_be_configured() -> None:
    assert Settings(quota_timezone="Europe/Berlin").quota_zone.key == "Europe/Berlin"


@pytest.mark.parametrize("zone", ["Mars/Olympus_Mons", "Asia/Kathmandu"])
def test_unsupported_quota_timezones_are_refused(zone: str) -> None:
    with pytest.raises(ValidationError, match="quota_timezone"):
        Settings(quota_timezone=zone)


def test_tests_never_see_real_api_keys() -> None:
    settings = Settings()
    assert settings.openai_api_key is None
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None
    assert not settings.tracing_enabled


def test_tracing_needs_both_keys() -> None:
    assert not Settings(langfuse_public_key="pk-lf-test").tracing_enabled  # type: ignore[arg-type]
    assert Settings(
        langfuse_public_key="pk-lf-test",  # type: ignore[arg-type]
        langfuse_secret_key="sk-lf-test",  # type: ignore[arg-type]
    ).tracing_enabled


SECRET_SENTINEL = "sk-proj-sentinel-0123456789abcdefghijklmnop"


def test_a_missing_setting_does_not_echo_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET_SENTINEL)

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)  # type: ignore[call-arg]

    # pydantic shortens long inputs in the middle, so a leak can be a prefix only:
    # check that no input is printed at all, not just that the whole key is absent.
    for text in (str(caught.value), repr(caught.value)):
        assert "jwt_secret" in text
        assert "input_value" not in text
        assert SECRET_SENTINEL[:8] not in text


def test_a_rejected_url_does_not_echo_its_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # Connection URLs are not SecretStr, so a rejected one would print its password.
    password = "pw-4821"
    monkeypatch.setenv("DATABASE_URL", f"mysql://u:{password}@db/app")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)  # type: ignore[call-arg]

    for text in (str(caught.value), repr(caught.value)):
        assert "database_url" in text
        assert "input_value" not in text
        assert password not in text


@pytest.mark.parametrize(
    "values",
    [{"demo_login_email": "demo@example.edu"}, {"demo_login_password": "demo-password-123"}],
)
def test_demo_login_needs_both_email_and_password(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="DEMO_LOGIN_EMAIL and DEMO_LOGIN_PASSWORD"):
        Settings(**values)  # type: ignore[arg-type]


def test_suppressed_email_domains_are_split_and_lowercased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_SUPPRESSED_DOMAINS", "Example.edu, example.org ,")

    assert Settings(_env_file=None).email_suppressed_domains == [  # type: ignore[call-arg]
        "example.edu",
        "example.org",
    ]
