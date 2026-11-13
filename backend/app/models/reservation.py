import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Text, false, func, text, true
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.server import Gpu, Server
    from app.models.user import User

PURPOSE_MAX_LENGTH: Final = 500
DECISION_REASON_MAX_LENGTH: Final = 500
ALLOCATION_OVERLAP_CONSTRAINT: Final = "ex_gpu_allocations_no_overlap"


class ReservationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    COMPLETED = "completed"


# Allocations of reservations in these statuses hold their GPUs.
HOLDING_STATUSES: Final = frozenset({ReservationStatus.PENDING, ReservationStatus.APPROVED})
# Reservations in these statuses count toward the weekly quota.
QUOTA_STATUSES: Final = frozenset(
    {ReservationStatus.PENDING, ReservationStatus.APPROVED, ReservationStatus.COMPLETED}
)


class CreatedVia(StrEnum):
    WEB = "web"
    ASSISTANT = "assistant"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Reservation(TimestampMixin, Base):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint("gpu_count >= 1", name="gpu_count_positive"),
        Index("ix_reservations_user_id_starts_at", "user_id", "starts_at"),
        Index("ix_reservations_server_id_starts_at", "server_id", "starts_at"),
        # The approval queue.
        Index(
            "ix_reservations_pending_starts_at",
            "starts_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # RESTRICT: users and servers with booking history are deactivated or retired instead.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="RESTRICT"))
    gpu_count: Mapped[int]
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status", values_callable=_enum_values)
    )
    purpose: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_via: Mapped[CreatedVia] = mapped_column(
        Enum(CreatedVia, name="reservation_created_via", values_callable=_enum_values),
        default=CreatedVia.WEB,
        server_default=CreatedVia.WEB.value,
    )
    # Booked by the demo seed or its nightly replay for a seeded student. The replay
    # deletes and recreates exactly these rows; nothing else is ever touched by it.
    seeded: Mapped[bool] = mapped_column(default=False, server_default=false())

    # Loaded explicitly; a lazy load in async code would fail at runtime.
    user: Mapped["User"] = relationship(foreign_keys=[user_id], lazy="raise")
    server: Mapped["Server"] = relationship(lazy="raise")
    allocations: Mapped[list["GpuAllocation"]] = relationship(
        back_populates="reservation", lazy="raise"
    )


class GpuAllocation(Base):
    """One GPU held by a reservation for its time range.

    `active` is true while the reservation is pending or approved and is cleared in the
    same transaction that moves it to any other status. The exclusion constraint makes
    the database the final authority on double-booking: no two active allocations of
    the same GPU may overlap.
    """

    __tablename__ = "gpu_allocations"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        ExcludeConstraint(
            ("gpu_id", "="),
            (text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            using="gist",
            where=text("active"),
            name=ALLOCATION_OVERLAP_CONSTRAINT,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE"), index=True
    )
    # RESTRICT: GPU rows that history refers to are soft-removed, never deleted.
    gpu_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gpus.id", ondelete="RESTRICT"))
    starts_at: Mapped[datetime]
    ends_at: Mapped[datetime]
    # NOT NULL matters: the exclusion constraint's WHERE (active) skips NULL rows.
    active: Mapped[bool] = mapped_column(default=True, server_default=true())

    reservation: Mapped[Reservation] = relationship(back_populates="allocations", lazy="raise")
    gpu: Mapped["Gpu"] = relationship(lazy="raise")
