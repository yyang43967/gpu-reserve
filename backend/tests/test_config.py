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
