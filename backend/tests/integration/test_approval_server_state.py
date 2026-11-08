"""Approval refuses a server that is no longer open for booking."""

from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ServerUnavailable
from app.models import Notification, Reservation, Server, ServerStatus, User, UserRole
from app.services import labs, reservations, servers
from tests.integration.reservation_helpers import add_lab, add_server, add_user, book

pytestmark = pytest.mark.integration

NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def restricted(db_session: AsyncSession) -> Server:
    lab = await add_lab(db_session, "Restricted Lab")
    return await add_server(db_session, lab, "h100-node-01", requires_approval=True)


async def _notifications(db: AsyncSession) -> list[Notification]:
    return list(await db.scalars(select(Notification)))


async def test_approval_refused_while_the_server_is_in_maintenance(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)
    await servers.update_server(db_session, restricted.id, status=ServerStatus.MAINTENANCE, now=NOW)
    await db_session.commit()

    pending_id = pending.id
    with pytest.raises(ServerUnavailable, match="h100-node-01"):
        await reservations.approve_reservation(db_session, admin, pending_id, now=NOW)
    await db_session.rollback()

    assert await _notifications(db_session) == []
    status = await db_session.scalar(select(Reservation.status).where(Reservation.id == pending_id))
    assert status == "pending"


async def test_approval_refused_when_the_lab_is_inactive(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)
    await labs.update_lab(db_session, admin, restricted.lab_id, is_active=False)
    await db_session.commit()

    with pytest.raises(ServerUnavailable):
        await reservations.approve_reservation(db_session, admin, pending.id, now=NOW)


async def test_rejection_still_allowed_while_the_server_is_in_maintenance(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)
    await servers.update_server(db_session, restricted.id, status=ServerStatus.MAINTENANCE, now=NOW)
    await db_session.commit()

    rejected = await reservations.reject_reservation(
        db_session, admin, pending.id, reason="Server down for repairs", now=NOW
    )

    assert rejected.status == "rejected"
