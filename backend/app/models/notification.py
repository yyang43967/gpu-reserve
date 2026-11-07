import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import Enum, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# A notification that has failed this many times is given up on.
MAX_DELIVERY_ATTEMPTS: Final = 5


class NotificationKind(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    # Also used when a running reservation is ended early by someone else.
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REMINDER = "reminder"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    # No longer relevant when its turn came, such as a reminder for a cancelled booking.
    SKIPPED = "skipped"
    # Gave up after MAX_DELIVERY_ATTEMPTS.
    FAILED = "failed"


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class Notification(Base):
    """An email owed to a user about one of their reservations: a transactional outbox.

    Rows are written in the same transaction as the change they report, so a committed
    change always has its notification, and a rolled-back one never does. A worker
    delivers them afterwards. At most one row of each kind exists per reservation.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("reservation_id", "kind", name="uq_notifications_reservation_kind"),
        # What the dispatcher scans: undelivered rows by due time.
        Index(
            "ix_notifications_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reservations.id", ondelete="CASCADE")
    )
    # RESTRICT, like reservations: users with booking history are never deleted.
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(NotificationKind, name="notification_kind", values_callable=_enum_values)
    )
    status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status", values_callable=_enum_values),
        default=NotificationStatus.PENDING,
        server_default=NotificationStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    sent_at: Mapped[datetime | None]
