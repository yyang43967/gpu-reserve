"""The notification outbox: which changes owe the owner an email, and what it says.

Rows are added in the caller's transaction, next to the change they report. Delivery
happens later in the worker (see app.services.jobs).

Who is told: the owner, whenever someone other than the owner changed their
reservation (an admin's approval, rejection, or cancellation, and cancellations caused
by deactivating the account), and always for expiry and reminders, which have no actor.
A student who cancels their own booking already knows, so no email is sent.
"""

import uuid
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Notification,
    NotificationKind,
    Reservation,
    ReservationStatus,
    Server,
)
from app.services.email import OutgoingEmail
from app.services.email_templates import ReservationFacts, render

RETRY_BASE_DELAY: Final = timedelta(minutes=1)
RETRY_MAX_DELAY: Final = timedelta(hours=1)
RESERVATIONS_PATH: Final = "/reservations"


def notify_owner(
    session: AsyncSession,
    reservation: Reservation,
    kind: NotificationKind,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
) -> None:
    """Queue `kind` for the reservation's owner, unless the owner made the change.

    `actor_id` is None for changes made by the system, which are always reported.
    """
    if actor_id is not None and actor_id == reservation.user_id:
        return
    session.add(
        Notification(
            reservation_id=reservation.id,
            recipient_user_id=reservation.user_id,
            kind=kind,
            next_attempt_at=now,
        )
    )


def retry_delay(attempts: int) -> timedelta:
    """How long to wait after the `attempts`-th failure: 1, 2, 4, 8 minutes, capped at
    an hour."""
    if attempts < 1:
        raise ValueError("attempts starts at 1")
    delay: timedelta = RETRY_BASE_DELAY * (2 ** (attempts - 1))
    return min(delay, RETRY_MAX_DELAY)


def is_stale(notification: Notification, reservation: Reservation, *, now: datetime) -> bool:
    """Whether the notification no longer describes anything worth telling.

    Only reminders can go stale: one for a reservation that is no longer approved, or
    that has already started, would be wrong or useless. The other kinds report a
    change that did happen, so they are sent even if the reservation changed again.
    """
    if notification.kind is not NotificationKind.REMINDER:
        return False
    return reservation.status is not ReservationStatus.APPROVED or reservation.starts_at <= now


async def prepare_email(
    session: AsyncSession, notification: Notification, *, now: datetime, frontend_url: str
) -> OutgoingEmail | None:
    """The email for `notification`, or None if it has gone stale (see is_stale)."""
    reservation = await session.scalar(
        select(Reservation)
        .where(Reservation.id == notification.reservation_id)
        .options(
            selectinload(Reservation.user),
            selectinload(Reservation.server).selectinload(Server.lab),
        )
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        return None
    if is_stale(notification, reservation, now=now):
        return None
    facts = ReservationFacts(
        recipient_name=reservation.user.full_name,
        recipient_email=reservation.user.email,
        hostname=reservation.server.hostname,
        lab_name=reservation.server.lab.name,
        timezone=reservation.server.lab.timezone,
        starts_at=reservation.starts_at,
        ends_at=reservation.ends_at,
        gpu_count=reservation.gpu_count,
        purpose=reservation.purpose,
        decision_reason=(
            reservation.decision_reason if notification.kind is NotificationKind.REJECTED else None
        ),
        ended_early=reservation.status is ReservationStatus.COMPLETED,
        link=frontend_url.rstrip("/") + RESERVATIONS_PATH,
    )
    return render(notification.kind, facts)
