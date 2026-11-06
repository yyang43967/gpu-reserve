import re
import uuid
from datetime import datetime
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.models import GpuModel, Server, ServerStatus
from app.models.server import (
    DEFAULT_MAX_BOOKING_HOURS,
    HOSTNAME_MAX_LENGTH,
    INTERCONNECT_MAX_LENGTH,
    MAX_BOOKING_HOURS_LIMIT,
    MAX_GPUS_PER_SERVER,
)
from app.models.user import OBJECT_KEY_MAX_LENGTH

# Sanity bounds that keep values far from the integer columns' limits.
MAX_CPU_CORES: Final = 1024
MAX_RAM_GB: Final = 65_536
NOTES_MAX_LENGTH: Final = 2000
# One DNS label: letters, digits, and inner hyphens.
_HOSTNAME_RE: Final = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")


def _valid_hostname(value: str) -> str:
    # Lowercased first: hostnames are case-insensitive and stored in lowercase.
    value = value.lower()
    if not _HOSTNAME_RE.fullmatch(value):
        raise ValueError("use letters, digits, and hyphens, not starting or ending with a hyphen")
    return value


Hostname = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=HOSTNAME_MAX_LENGTH),
    AfterValidator(_valid_hostname),
]
GpuCount = Annotated[int, Field(ge=1, le=MAX_GPUS_PER_SERVER)]
CpuCores = Annotated[int, Field(ge=1, le=MAX_CPU_CORES)]
RamGb = Annotated[int, Field(ge=1, le=MAX_RAM_GB)]
MaxBookingHours = Annotated[int, Field(ge=1, le=MAX_BOOKING_HOURS_LIMIT)]
Interconnect = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=INTERCONNECT_MAX_LENGTH)
]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=NOTES_MAX_LENGTH)]
# A key returned by POST /uploads/presign for a server image.
ImageKey = Annotated[str, StringConstraints(min_length=1, max_length=OBJECT_KEY_MAX_LENGTH)]


class ServerCreate(BaseModel):
    """GPU memory is not sent: it follows from the GPU model."""

    model_config = ConfigDict(extra="forbid")

    lab_id: uuid.UUID
    hostname: Hostname
    gpu_model: GpuModel
    gpu_count: GpuCount
    cpu_cores: CpuCores
    ram_gb: RamGb
    interconnect: Interconnect | None = None
    status: ServerStatus = ServerStatus.AVAILABLE
    requires_approval: bool = False
    max_booking_hours: MaxBookingHours = DEFAULT_MAX_BOOKING_HOURS
    notes: Notes | None = None
    image_key: ImageKey | None = None


class ServerUpdate(BaseModel):
    """Omitted fields are left unchanged. `interconnect`, `notes`, and `image_key` set to
    null clear them."""

    model_config = ConfigDict(extra="forbid")

    lab_id: uuid.UUID | None = None
    hostname: Hostname | None = None
    gpu_model: GpuModel | None = None
    gpu_count: GpuCount | None = None
    cpu_cores: CpuCores | None = None
    ram_gb: RamGb | None = None
    interconnect: Interconnect | None = None
    status: ServerStatus | None = None
    requires_approval: bool | None = None
    max_booking_hours: MaxBookingHours | None = None
    notes: Notes | None = None
    image_key: ImageKey | None = None


class LabSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    is_active: bool


class ServerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lab: LabSummary
    hostname: str
    gpu_model: GpuModel
    gpu_memory_gb: int
    gpu_count: int
    cpu_cores: int
    ram_gb: int
    interconnect: str | None
    status: ServerStatus
    requires_approval: bool
    max_booking_hours: int
    notes: str | None
    # A presigned download URL, or None without an image.
    image_url: str | None = None
    created_at: datetime

    @classmethod
    def build(cls, server: Server, image_url: str | None) -> "ServerRead":
        return cls.model_validate(server).model_copy(update={"image_url": image_url})
