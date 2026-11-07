import uuid
from datetime import datetime
from typing import Annotated, Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models import GpuModel
from app.models.server import MAX_GPUS_PER_SERVER
from app.schemas.reservation import ReservationLab
from app.services.slot_search import SlotCandidate, SlotQuery

SLOT_MINUTES: Final = 30
# A week; no server allows a single booking that long, so this only bounds the input.
MAX_DURATION_MINUTES: Final = 7 * 24 * 60


class SlotSearchRequest(BaseModel):
    """Find times for `gpu_count` GPUs for `duration_minutes`. Every result starts at
    or after `earliest` and ends at or before `latest`."""

    model_config = ConfigDict(extra="forbid")

    gpu_count: Annotated[int, Field(ge=1, le=MAX_GPUS_PER_SERVER)]
    duration_minutes: Annotated[
        int,
        Field(
            ge=SLOT_MINUTES,
            le=MAX_DURATION_MINUTES,
            multiple_of=SLOT_MINUTES,
            description="Length of the job, in whole half hours.",
        ),
    ]
    earliest: Annotated[
        AwareDatetime,
        Field(description="Earliest start. Times in the past are moved to the next half hour."),
    ]
    latest: Annotated[
        AwareDatetime,
        Field(
            description=(
                "Latest end: a result's end is at or before this time. The window from "
                "`earliest` to `latest` is at most 14 days."
            )
        ),
    ]
    gpu_model: GpuModel | None = None
    min_gpu_memory_gb: Annotated[int | None, Field(ge=1, description="Memory per GPU.")] = None

    def to_query(self) -> SlotQuery:
        return SlotQuery(
            gpu_count=self.gpu_count,
            duration_minutes=self.duration_minutes,
            earliest=self.earliest,
            latest=self.latest,
            gpu_model=self.gpu_model,
            min_gpu_memory_gb=self.min_gpu_memory_gb,
        )


class SlotServer(BaseModel):
    id: uuid.UUID
    hostname: str
    gpu_model: GpuModel
    gpu_memory_gb: int
    gpu_count: int
    requires_approval: bool
    max_booking_hours: int


class SlotRead(BaseModel):
    server: SlotServer
    lab: ReservationLab
    starts_at: datetime
    ends_at: datetime
    # The fewest GPUs free during the slot, the requested ones included.
    free_gpus: int

    @classmethod
    def build(cls, candidate: SlotCandidate) -> Self:
        server = candidate.server
        return cls(
            server=SlotServer(
                id=server.id,
                hostname=server.hostname,
                gpu_model=server.gpu_model,
                gpu_memory_gb=server.gpu_memory_gb,
                gpu_count=server.gpu_count,
                requires_approval=server.requires_approval,
                max_booking_hours=server.max_booking_hours,
            ),
            lab=ReservationLab(
                id=server.lab.id, name=server.lab.name, timezone=server.lab.timezone
            ),
            starts_at=candidate.starts_at,
            ends_at=candidate.ends_at,
            free_gpus=candidate.free_gpus,
        )


class SlotSearchResponse(BaseModel):
    """Ranked: earliest start, then servers that need no approval, then the smallest
    GPUs that fit, then the tightest fit. At most 3 per server and 20 in all."""

    items: list[SlotRead]
