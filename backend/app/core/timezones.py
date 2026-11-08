"""Time zone names the application accepts, for labs and for the quota week.

Booking times sit on a 30-minute grid in UTC. That grid lands on local :00 and :30 only
where the UTC offset is a whole number of half hours, so other zones are refused.
"""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo, available_timezones

_OFFSET_STEP: Final = timedelta(minutes=30)


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def offsets_on_half_hour_grid(zone: ZoneInfo) -> bool:
    """Check the offsets in January and July of this year and next, which covers
    standard and daylight saving time in both hemispheres."""
    this_year = datetime.now(UTC).year
    for year in (this_year, this_year + 1):
        for month in (1, 7):
            offset = datetime(year, month, 1, tzinfo=zone).utcoffset()
            if offset is None or offset % _OFFSET_STEP:
                return False
    return True


def valid_timezone(value: str) -> str:
    """Return `value` if it names a supported zone; raise ValueError otherwise."""
    if value not in _known_timezones():
        raise ValueError("must be an IANA time zone name, such as America/Los_Angeles")
    if not offsets_on_half_hour_grid(ZoneInfo(value)):
        raise ValueError("time zones with offsets that are not whole half hours are not supported")
    return value
