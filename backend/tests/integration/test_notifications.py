"""Which reservation changes queue an email, written in the same transaction."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Notification,
    NotificationKind,
    NotificationStatus,
    Reservation,
    Server,
    User,
    UserRole,
)
from app.services import reservations, users
from tests.integration.reservation_helpers import add_lab, add_server, add_user, book

pytestmark = pytest.mark.integration

# Monday 7 January 2030, 10:00 in Los Angeles; bookings are that Saturday morning.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def open_server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), "a100-node-01")


@pytest.fixture
async def restricted(db_session: AsyncSession) -> Server:
    lab = await add_lab(db_session, "Restricted Lab")
    return await add_server(db_session, lab, "h100-node-01", requires_approval=True)


async def _notifications(db: AsyncSession) -> list[Notification]:
    return list(
        await db.scalars(
            select(Notification)
            .order_by(Notification.created_at, Notification.kind)
            .execution_options(populate_existing=True)
        )
    )


async def test_admin_approval_notifies_the_owner(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    await reservations.approve_reservation(db_session, admin, pending.id, now=NOW)
    await db_session.commit()

    [note] = await _notifications(db_session)
    assert note.kind is NotificationKind.APPROVED
    assert note.reservation_id == pending.id
    assert note.recipient_user_id == student.id
    assert note.status is NotificationStatus.PENDING
    assert note.attempts == 0
    assert note.next_attempt_at == NOW


async def test_rejection_notifies_the_owner(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    await reservations.reject_reservation(
        db_session, admin, pending.id, reason="Course deadline week", now=NOW
    )
    await db_session.commit()

    assert [n.kind for n in await _notifications(db_session)] == [NotificationKind.REJECTED]


async def test_an_admin_deciding_their_own_request_is_not_notified(
    db_session: AsyncSession, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, admin, restricted, SATURDAY, now=NOW)

    await reservations.approve_reservation(db_session, admin, pending.id, now=NOW)
    await db_session.commit()

    assert await _notifications(db_session) == []


async def test_admin_cancellation_notifies_the_owner(
    db_session: AsyncSession, student: User, admin: User, open_server: Server
) -> None:
    booked = await book(db_session, student, open_server, SATURDAY, now=NOW)

    await reservations.cancel_reservation(db_session, admin, booked.id, now=NOW)
    await db_session.commit()

    [note] = await _notifications(db_session)
    assert (note.kind, note.recipient_user_id) == (NotificationKind.CANCELLED, student.id)


async def test_admin_ending_a_running_reservation_notifies_the_owner(
    db_session: AsyncSession, student: User, admin: User, open_server: Server
) -> None:
    booked = await book(db_session, student, open_server, SATURDAY, now=NOW, hours=2)

    await reservations.cancel_reservation(
        db_session, admin, booked.id, now=SATURDAY + timedelta(minutes=40)
    )
    await db_session.commit()

    assert [n.kind for n in await _notifications(db_session)] == [NotificationKind.CANCELLED]


async def test_cancelling_your_own_booking_sends_nothing(
    db_session: AsyncSession, student: User, open_server: Server
) -> None:
    upcoming = await book(db_session, student, open_server, SATURDAY, now=NOW)
    running = await book(db_session, student, open_server, SATURDAY + timedelta(days=1), now=NOW)

    await reservations.cancel_reservation(db_session, student, upcoming.id, now=NOW)
    await reservations.cancel_reservation(
        db_session, student, running.id, now=SATURDAY + timedelta(days=1, minutes=10)
    )
    await db_session.commit()

    assert await _notifications(db_session) == []


async def test_deactivation_notifies_the_deactivated_user_of_each_release(
    db_session: AsyncSession, student: User, admin: User, open_server: Server, restricted: Server
) -> None:
    """The account is inactive by the time the email goes out, but the person still
    needs to know their bookings were released."""
    approved = await book(db_session, student, open_server, SATURDAY, now=NOW)
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    await users.update_user(db_session, admin, student.id, is_active=False, now=NOW)
    await db_session.commit()

    notes = await _notifications(db_session)
    assert {(n.reservation_id, n.kind) for n in notes} == {
        (approved.id, NotificationKind.CANCELLED),
        (pending.id, NotificationKind.CANCELLED),
    }
    assert {n.recipient_user_id for n in notes} == {student.id}


async def test_notifications_roll_back_with_the_change(
    db_session: AsyncSession, student: User, admin: User, open_server: Server
) -> None:
    booked_id = (await book(db_session, student, open_server, SATURDAY, now=NOW)).id

    await reservations.cancel_reservation(db_session, admin, booked_id, now=NOW)
    await db_session.flush()
    assert len(await _notifications(db_session)) == 1
    await db_session.rollback()

    assert await _notifications(db_session) == []
    status = await db_session.scalar(select(Reservation.status).where(Reservation.id == booked_id))
    assert status == "approved"
