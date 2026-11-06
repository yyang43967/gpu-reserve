import uuid
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Final

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


def _valid_timezone(value: str) -> str:
    if value not in _known_timezones():
        raise ValueError("must be an IANA time zone name, such as America/Los_Angeles")
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
