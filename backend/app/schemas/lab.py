import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated, Final
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.models.lab import (
    DEFAULT_LAB_TIMEZONE,
    LAB_LOCATION_MAX_LENGTH,
    LAB_NAME_MAX_LENGTH,
)

LAB_DESCRIPTION_MAX_LENGTH: Final = 2000


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset[str]:
    from zoneinfo import available_timezones

    return frozenset(available_timezones())


# Booking times sit on a 30-minute grid in UTC. That grid lands on local :00 and :30 only
# where the UTC offset is a whole number of half hours.
_OFFSET_STEP: Final = timedelta(minutes=30)


def _offsets_on_half_hour_grid(zone: ZoneInfo) -> bool:
    """Check the offsets in January and July of this year and next, which covers
    standard and daylight saving time in both hemispheres."""
    this_year = datetime.now(UTC).year
    for year in (this_year, this_year + 1):
        for month in (1, 7):
            offset = datetime(year, month, 1, tzinfo=zone).utcoffset()
            if offset is None or offset % _OFFSET_STEP:
                return False
    return True


def _valid_timezone(value: str) -> str:
    if value not in _known_timezones():
        raise ValueError("must be an IANA time zone name, such as America/Los_Angeles")
    if not _offsets_on_half_hour_grid(ZoneInfo(value)):
        raise ValueError("time zones with offsets that are not whole half hours are not supported")
    return value


LabName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LAB_NAME_MAX_LENGTH)
]
Location = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=LAB_LOCATION_MAX_LENGTH),
]
Timezone = Annotated[str, AfterValidator(_valid_timezone)]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=LAB_DESCRIPTION_MAX_LENGTH)
]


class LabCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: LabName
    location: Location
    timezone: Timezone = DEFAULT_LAB_TIMEZONE
    description: Description | None = None
    is_active: bool = True


class LabUpdate(BaseModel):
    """Omitted fields are left unchanged. `description: null` clears the description."""

    model_config = ConfigDict(extra="forbid")

    name: LabName | None = None
    location: Location | None = None
    timezone: Timezone | None = None
    description: Description | None = None
    is_active: bool | None = None


class LabRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    location: str
    timezone: str
    description: str | None
    is_active: bool
    created_at: datetime
