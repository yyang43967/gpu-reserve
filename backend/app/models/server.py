import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.models.user import OBJECT_KEY_MAX_LENGTH

if TYPE_CHECKING:
    from app.models.lab import Lab

# One DNS label (RFC 1123), e.g. "a100-node-01".
HOSTNAME_MAX_LENGTH: Final = 63
INTERCONNECT_MAX_LENGTH: Final = 50
MAX_GPUS_PER_SERVER: Final = 16
DEFAULT_MAX_BOOKING_HOURS: Final = 12
# Reservations start at most 14 days ahead, so a longer limit could never be used.
MAX_BOOKING_HOURS_LIMIT: Final = 14 * 24


class GpuModel(StrEnum):
    RTX_4090 = "RTX_4090"
    RTX_6000_ADA = "RTX_6000_ADA"
    L40S = "L40S"
    A100_40GB = "A100_40GB"
    A100_80GB = "A100_80GB"
    H100_80GB = "H100_80GB"


# Memory per GPU. The server's gpu_memory_gb is always derived from its model.
GPU_MEMORY_GB: Final[dict[GpuModel, int]] = {
    GpuModel.RTX_4090: 24,
    GpuModel.RTX_6000_ADA: 48,
    GpuModel.L40S: 48,
    GpuModel.A100_40GB: 40,
    GpuModel.A100_80GB: 80,
    GpuModel.H100_80GB: 80,
}


class ServerStatus(StrEnum):
    AVAILABLE = "available"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Server(TimestampMixin, Base):
    __tablename__ = "servers"
    __table_args__ = (
        CheckConstraint(f"gpu_count BETWEEN 1 AND {MAX_GPUS_PER_SERVER}", name="gpu_count_range"),
        CheckConstraint("gpu_memory_gb > 0", name="gpu_memory_positive"),
        CheckConstraint("cpu_cores > 0", name="cpu_cores_positive"),
        CheckConstraint("ram_gb > 0", name="ram_positive"),
        CheckConstraint(
            f"max_booking_hours BETWEEN 1 AND {MAX_BOOKING_HOURS_LIMIT}",
            name="max_booking_hours_range",
        ),
        CheckConstraint("hostname = lower(hostname)", name="hostname_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # RESTRICT: a lab with servers cannot be deleted; the service reports it as a conflict.
    lab_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("labs.id", ondelete="RESTRICT"), index=True
    )
    hostname: Mapped[str] = mapped_column(String(HOSTNAME_MAX_LENGTH), unique=True)
    gpu_model: Mapped[GpuModel] = mapped_column(
        Enum(GpuModel, name="gpu_model", values_callable=_enum_values)
    )
    gpu_memory_gb: Mapped[int]
    # Always equal to the number of GPU rows with removed_at IS NULL.
    gpu_count: Mapped[int]
    cpu_cores: Mapped[int]
    ram_gb: Mapped[int]
    interconnect: Mapped[str | None] = mapped_column(String(INTERCONNECT_MAX_LENGTH))
    status: Mapped[ServerStatus] = mapped_column(
        Enum(ServerStatus, name="server_status", values_callable=_enum_values),
        default=ServerStatus.AVAILABLE,
        server_default=ServerStatus.AVAILABLE.value,
    )
    requires_approval: Mapped[bool] = mapped_column(default=False, server_default=false())
    max_booking_hours: Mapped[int] = mapped_column(
        default=DEFAULT_MAX_BOOKING_HOURS,
        server_default=text(str(DEFAULT_MAX_BOOKING_HOURS)),
    )
    notes: Mapped[str | None] = mapped_column(Text)
    image_key: Mapped[str | None] = mapped_column(String(OBJECT_KEY_MAX_LENGTH))

    # Loaded explicitly; a lazy load in async code would fail at runtime.
    lab: Mapped["Lab"] = relationship(lazy="raise")


class Gpu(Base):
    """One GPU slot of a server, numbered from 0.

    Lowering a server's gpu_count soft-removes its highest-numbered GPUs by setting
    `removed_at`, so rows that reservations will refer to are never deleted. Raising it
    again reactivates removed rows before inserting new ones.
    """

    __tablename__ = "gpus"
    __table_args__ = (
        CheckConstraint('"index" >= 0', name="index_non_negative"),
        # Only one active GPU per (server, index); removed rows do not count.
        Index(
            "uq_gpus_server_id_index_active",
            "server_id",
            "index",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("servers.id", ondelete="CASCADE"), index=True
    )
    index: Mapped[int]
    removed_at: Mapped[datetime | None]
