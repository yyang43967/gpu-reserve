"""expire_pending: pending reservations whose start has passed become expired.

These tests describe the job before it exists. They cover the outcome, idempotency,
the job lock, and both orders of a race with an admin's approval, without assuming
how the job is written (one statement or several, waiting on locked rows or skipping
them).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.exceptions import InvalidReservationState
from app.models import (
    AuditLog,
    GpuAllocation,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reservation,
    ReservationStatus,
    Server,
    User,
    UserRole,
)
from app.services import jobs, reservations
from tests.integration.concurrency import LOCK_WAIT_TIMEOUT, race_pair
from tests.integration.reservation_helpers import add_lab, add_server, add_user, book

pytestmark = pytest.mark.integration

# Monday 7 January 2030, 10:00 in Los Angeles; bookings are that Saturday morning.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
JUST_BEFORE: Final = SATURDAY - timedelta(minutes=1)


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def restricted(db_session: AsyncSession) -> Server:
    lab = await add_lab(db_session)
    return await add_server(db_session, lab, "h100-node-01", gpu_count=1, requires_approval=True)


@pytest.fixture
async def open_server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session, "Open Lab"), "a100-node-01")


async def _state(
    db: AsyncSession, reservation_id: uuid.UUID
) -> tuple[ReservationStatus, list[bool]]:
    status = await db.scalar(
        select(Reservation.status)
        .where(Reservation.id == reservation_id)
        .execution_options(populate_existing=True)
    )
    active = list(
        await db.scalars(
            select(GpuAllocation.active).where(GpuAllocation.reservation_id == reservation_id)
        )
    )
    assert status is not None
    return status, active


async def _expired_notifications(db: AsyncSession) -> list[Notification]:
    return list(
        await db.scalars(select(Notification).where(Notification.kind == NotificationKind.EXPIRED))
    )


async def _audit_count(db: AsyncSession, reservation_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.entity_id == reservation_id, AuditLog.action == "reservation.expired")
    )
    return count or 0


# --- outcome ---


async def test_expires_pending_reservations_whose_start_has_passed(
    db_session: AsyncSession, student: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    changed = await jobs.expire_pending(db_session, now=SATURDAY)
    await db_session.commit()

    assert changed == 1
    assert await _state(db_session, pending.id) == (ReservationStatus.EXPIRED, [False])
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_id == pending.id, AuditLog.action == "reservation.expired"
        )
    )
    assert audit is not None
    assert audit.actor_id is None
    assert audit.entity_type == "reservation"
    assert audit.payload == {"from_status": "pending", "to_status": "expired"}
    [note] = await _expired_notifications(db_session)
    assert note.reservation_id == pending.id
    assert note.recipient_user_id == student.id
    assert note.status is NotificationStatus.PENDING
    assert note.next_attempt_at == SATURDAY


async def test_leaves_everything_else_alone(
    db_session: AsyncSession, student: User, restricted: Server, open_server: Server
) -> None:
    later_pending = await book(
        db_session, student, restricted, SATURDAY + timedelta(hours=2), now=NOW
    )
    started_approved = await book(db_session, student, open_server, SATURDAY, now=NOW)

    changed = await jobs.expire_pending(db_session, now=SATURDAY + timedelta(minutes=30))
    await db_session.commit()

    assert changed == 0
    assert await _state(db_session, later_pending.id) == (ReservationStatus.PENDING, [True])
    assert await _state(db_session, started_approved.id) == (ReservationStatus.APPROVED, [True])
    assert await _expired_notifications(db_session) == []


async def test_expired_gpus_can_be_booked_again(
    db_session: AsyncSession, student: User, restricted: Server
) -> None:
    """The server has one GPU. Once the pending request expires, the rest of its time
    is free for someone else."""
    await book(db_session, student, restricted, SATURDAY, now=NOW, hours=3)
    await jobs.expire_pending(db_session, now=SATURDAY + timedelta(minutes=10))
    await db_session.commit()
    other = await add_user(db_session, "other@example.edu")

    rebooked = await book(
        db_session,
        other,
        restricted,
        SATURDAY + timedelta(hours=1),
        now=SATURDAY + timedelta(minutes=10),
    )

    assert rebooked.status is ReservationStatus.PENDING


async def test_running_twice_changes_nothing_the_second_time(
    db_session: AsyncSession, student: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    assert await jobs.expire_pending(db_session, now=SATURDAY) == 1
    await db_session.commit()
    assert await jobs.expire_pending(db_session, now=SATURDAY + timedelta(minutes=1)) == 0
    await db_session.commit()

    assert await _audit_count(db_session, pending.id) == 1
    assert len(await _expired_notifications(db_session)) == 1


# --- the job lock ---


async def test_skipped_while_another_run_holds_the_lock(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with job_factory() as holder, job_factory() as other:
        assert await jobs.try_job_lock(holder, jobs.Job.EXPIRE_PENDING)

        assert await jobs.expire_pending(other, now=SATURDAY) is None
        await other.rollback()
        await holder.rollback()


# --- racing an admin's approval ---


async def _pending_with_admin(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, User]:
    async with factory() as db:
        lab = await add_lab(db)
        server = await add_server(db, lab, "h100-node-01", requires_approval=True)
        student = await add_user(db, "student@example.edu")
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)
        pending = await book(db, student, server, SATURDAY, now=NOW)
    return pending.id, admin


async def test_approval_committed_first_wins(
    job_factory: async_sessionmaker[AsyncSession], job_engine: AsyncEngine
) -> None:
    """The admin approves a minute before the start and holds the row while the job
    runs at the start. The approval stands with its GPUs held, and nothing expires."""
    reservation_id, admin = await _pending_with_admin(job_factory)
    async with job_factory() as admin_session, job_factory() as job_session:
        await reservations.approve_reservation(
            admin_session, admin, reservation_id, now=JUST_BEFORE
        )
        outcome = await race_pair(
            job_engine,
            admin_session,
            job_session,
            lambda: jobs.expire_pending(job_session, now=SATURDAY),
        )

    assert outcome.error is None
    assert outcome.result == 0
    async with job_factory() as db:
        assert await _state(db, reservation_id) == (ReservationStatus.APPROVED, [True])
        assert await _expired_notifications(db) == []
        assert await _audit_count(db, reservation_id) == 0


async def test_expiry_committed_first_wins(
    job_factory: async_sessionmaker[AsyncSession], job_engine: AsyncEngine
) -> None:
    """The job expires the row and holds it; an approval arriving meanwhile (with a
    clock a minute behind) waits, then finds the reservation expired and is refused."""
    reservation_id, admin = await _pending_with_admin(job_factory)
    async with job_factory() as job_session, job_factory() as admin_session:
        assert await jobs.expire_pending(job_session, now=SATURDAY) == 1
        outcome = await race_pair(
            job_engine,
            job_session,
            admin_session,
            lambda: reservations.approve_reservation(
                admin_session, admin, reservation_id, now=JUST_BEFORE
            ),
        )

    assert outcome.blocked
    assert isinstance(outcome.error, InvalidReservationState)
    async with job_factory() as db:
        assert await _state(db, reservation_id) == (ReservationStatus.EXPIRED, [False])
        assert len(await _expired_notifications(db)) == 1


async def test_control_approval_without_its_row_lock_overwrites_the_expiry(
    job_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: if approval read the row without FOR UPDATE, it could read
    `pending`, let the job expire the row and release the GPUs, then write `approved`
    over it. The result is an approved reservation that holds no GPUs, which is
    exactly what the row lock prevents."""
    reservation_id, admin = await _pending_with_admin(job_factory)
    read_done = asyncio.Event()
    expired = asyncio.Event()

    async def read_then_wait(session: AsyncSession, viewer: User, rid: uuid.UUID) -> Reservation:
        reservation = await session.scalar(
            select(Reservation)
            .where(Reservation.id == rid)
            .execution_options(populate_existing=True)
        )
        assert reservation is not None
        read_done.set()
        await asyncio.wait_for(expired.wait(), LOCK_WAIT_TIMEOUT)
        return reservation

    monkeypatch.setattr(reservations, "_lock_reservation", read_then_wait)

    async def approve() -> None:
        async with job_factory() as admin_session:
            await reservations.approve_reservation(
                admin_session, admin, reservation_id, now=JUST_BEFORE
            )
            await admin_session.commit()

    task = asyncio.create_task(approve())
    try:
        await asyncio.wait_for(read_done.wait(), LOCK_WAIT_TIMEOUT)
        async with job_factory() as job_session:
            assert await jobs.expire_pending(job_session, now=SATURDAY) == 1
            await job_session.commit()
    finally:
        # Release the approval whatever happened, so no task outlives the test.
        expired.set()
        await asyncio.wait({task}, timeout=LOCK_WAIT_TIMEOUT)
    task.result()

    async with job_factory() as db:
        assert await _state(db, reservation_id) == (ReservationStatus.APPROVED, [False])
