import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from app.models import CreatedVia, GpuModel, Reservation, ReservationStatus
from app.models.reservation import DECISION_REASON_MAX_LENGTH, PURPOSE_MAX_LENGTH
from app.models.server import MAX_GPUS_PER_SERVER

Purpose = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=PURPOSE_MAX_LENGTH)
]


class ReservationCreate(BaseModel):
    """Times are ISO 8601 with an offset and must fall on the hour or half hour."""

    model_config = ConfigDict(extra="forbid")

    server_id: uuid.UUID
    gpu_count: Annotated[int, Field(ge=1, le=MAX_GPUS_PER_SERVER)]
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    purpose: Purpose


class ReservationServer(BaseModel):
    id: uuid.UUID
    hostname: str
    gpu_model: GpuModel


class ReservationLab(BaseModel):
    id: uuid.UUID
    name: str
    timezone: str


class ReservationUser(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str


class ReservationRead(BaseModel):
    id: uuid.UUID
    status: ReservationStatus
    gpu_count: int
    starts_at: datetime
    ends_at: datetime
    purpose: str
    created_via: CreatedVia
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    server: ReservationServer
    lab: ReservationLab
    user: ReservationUser
    # Numbers of the GPUs this reservation holds or held.
    gpu_indexes: list[int]

    @classmethod
    def build(cls, reservation: Reservation) -> Self:
        server = reservation.server
        return cls(
            id=reservation.id,
            status=reservation.status,
            gpu_count=reservation.gpu_count,
            starts_at=reservation.starts_at,
            ends_at=reservation.ends_at,
            purpose=reservation.purpose,
            created_via=reservation.created_via,
            decided_at=reservation.decided_at,
            decision_reason=reservation.decision_reason,
            created_at=reservation.created_at,
            server=ReservationServer(
                id=server.id, hostname=server.hostname, gpu_model=server.gpu_model
            ),
            lab=ReservationLab(
                id=server.lab.id, name=server.lab.name, timezone=server.lab.timezone
            ),
            user=ReservationUser(
                id=reservation.user.id,
                full_name=reservation.user.full_name,
                email=reservation.user.email,
            ),
            gpu_indexes=sorted(allocation.gpu.index for allocation in reservation.allocations),
        )


DecisionReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=DECISION_REASON_MAX_LENGTH),
]


class ReservationReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: DecisionReason


class FreeRunRead(BaseModel):
    starts_at: datetime
    ends_at: datetime
    free: int


class AvailabilityRead(BaseModel):
    server_id: uuid.UUID
    # The lab's zone, for display.
    timezone: str
    gpu_count: int
    slot_minutes: int
    starts_at: datetime
    ends_at: datetime
    runs: list[FreeRunRead]


class QuotaRead(BaseModel):
    """GPU-hours in the current quota week. Hours can be fractional (bookings are in
    half hours), so they are numbers, not integers."""

    week_start: datetime
    week_end: datetime
    timezone: str
    used_hours: float
    quota_hours: float
    remaining_hours: float
