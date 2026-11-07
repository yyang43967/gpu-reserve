"""The sweeping jobs that are not under design review: completing finished
reservations and queueing reminders, plus the job lock they share."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    AuditLog,
    GpuAllocation,
    Notification,
    NotificationKind,
    Reservation,
    ReservationStatus,
    Server,
    User,
    UserRole,
)
from app.services import jobs, reservations
from tests.integration.concurrency import run_together
from tests.integration.reservation_helpers import add_lab, add_server, add_user, book

pytestmark = pytest.mark.integration

# Monday 7 January 2030, 10:00 in Los Angeles; bookings are that Saturday morning.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
H: Final = timedelta(hours=1)


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), gpu_count=4)


@pytest.fixture
async def restricted(db_session: AsyncSession) -> Server:
    lab = await add_lab(db_session, "Restricted Lab")
    return await add_server(db_session, lab, "h100-node-01", requires_approval=True)


async def _status(db: AsyncSession, reservation: Reservation) -> ReservationStatus:
    status = await db.scalar(
        select(Reservation.status)
        .where(Reservation.id == reservation.id)
        .execution_options(populate_existing=True)
    )
    assert status is not None
    return status


async def _active(db: AsyncSession, reservation: Reservation) -> list[bool]:
    return list(
        await db.scalars(
            select(GpuAllocation.active).where(GpuAllocation.reservation_id == reservation.id)
        )
    )


async def _count(db: AsyncSession, model: type[object], *conditions: object) -> int:
    return await db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0  # type: ignore[arg-type]


async def test_audit_actor_is_nullable_for_system_rows(db_session: AsyncSession) -> None:
    nullable = await db_session.scalar(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'audit_log' AND column_name = 'actor_id'"
        )
    )
    assert nullable == "YES"


# --- complete_finished ---


async def test_completes_approved_reservations_whose_end_has_passed(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    ended = await book(db_session, student, server, SATURDAY, now=NOW, hours=2)
    running = await book(db_session, student, server, SATURDAY + H, now=NOW, hours=3)
    later = await book(db_session, student, server, SATURDAY + timedelta(days=1), now=NOW)

    changed = await jobs.complete_finished(db_session, now=SATURDAY + 2 * H)
    await db_session.commit()

    assert changed == 1
    assert await _status(db_session, ended) is ReservationStatus.COMPLETED
    assert await _active(db_session, ended) == [False]
    assert await _status(db_session, running) is ReservationStatus.APPROVED
    assert await _active(db_session, running) == [True]
    assert await _status(db_session, later) is ReservationStatus.APPROVED
    audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.entity_id == ended.id)
        .where(AuditLog.action == "reservation.completed")
    )
    assert audit is not None
    assert audit.actor_id is None
    assert audit.payload == {"from_status": "approved", "to_status": "completed"}


async def test_completion_ignores_pending_and_sends_no_email(
    db_session: AsyncSession, student: User, restricted: Server
) -> None:
    pending = await book(db_session, student, restricted, SATURDAY, now=NOW)

    changed = await jobs.complete_finished(db_session, now=SATURDAY + 2 * H)
    await db_session.commit()

    assert changed == 0
    assert await _status(db_session, pending) is ReservationStatus.PENDING
    assert await _count(db_session, Notification) == 0


async def test_completion_is_idempotent(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    ended = await book(db_session, student, server, SATURDAY, now=NOW)
    later = SATURDAY + 2 * H

    assert await jobs.complete_finished(db_session, now=later) == 1
    await db_session.commit()
    assert await jobs.complete_finished(db_session, now=later) == 0
    await db_session.commit()

    assert (
        await _count(
            db_session,
            AuditLog,
            AuditLog.entity_id == ended.id,
            AuditLog.action == "reservation.completed",
        )
        == 1
    )


async def test_a_completed_reservation_cannot_be_ended_early_afterwards(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    """The job and a late cancel act on the same row; the second one finds it done."""
    from app.core.exceptions import InvalidReservationState

    ended = await book(db_session, student, server, SATURDAY, now=NOW)
    await jobs.complete_finished(db_session, now=SATURDAY + H)
    await db_session.commit()

    with pytest.raises(InvalidReservationState):
        await reservations.cancel_reservation(db_session, student, ended.id, now=SATURDAY + H)


# --- queue_reminders ---


async def test_reminders_for_approved_reservations_starting_within_the_hour(
    db_session: AsyncSession, student: User, server: Server, restricted: Server
) -> None:
    soon = await book(db_session, student, server, SATURDAY, now=NOW)
    await book(db_session, student, server, SATURDAY + 2 * H, now=NOW)  # too far
    await book(db_session, student, restricted, SATURDAY, now=NOW)  # pending: no reminder
    at = SATURDAY - H  # exactly an hour before `soon`

    queued = await jobs.queue_reminders(db_session, now=at)
    await db_session.commit()

    assert queued == 1
    [note] = list(await db_session.scalars(select(Notification)))
    assert (note.reservation_id, note.kind) == (soon.id, NotificationKind.REMINDER)
    assert note.recipient_user_id == student.id
    assert note.next_attempt_at == at


async def test_no_reminder_once_the_reservation_has_started(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await book(db_session, student, server, SATURDAY, now=NOW)

    assert await jobs.queue_reminders(db_session, now=SATURDAY) == 0


async def test_a_reservation_gets_one_reminder_across_runs(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await book(db_session, student, server, SATURDAY, now=NOW)

    for minutes_before in (55, 50, 45):
        await jobs.queue_reminders(db_session, now=SATURDAY - timedelta(minutes=minutes_before))
        await db_session.commit()

    assert await _count(db_session, Notification) == 1


async def test_a_late_booking_is_reminded_on_the_next_run(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    """Booked 40 minutes before its start, after the last run: the next run, five
    minutes later, still finds it inside the hour."""
    booked_at = SATURDAY - timedelta(minutes=40)
    await book(db_session, student, server, SATURDAY, now=booked_at)

    assert await jobs.queue_reminders(db_session, now=booked_at + timedelta(minutes=5)) == 1


# --- the job lock ---


@pytest.mark.parametrize("job", [jobs.complete_finished, jobs.queue_reminders])
async def test_a_run_is_skipped_while_another_holds_the_lock(
    job_factory: async_sessionmaker[AsyncSession],
    job: Callable[..., object],
) -> None:
    name = jobs.Job.COMPLETE_FINISHED if job is jobs.complete_finished else jobs.Job.QUEUE_REMINDERS
    async with job_factory() as holder, job_factory() as other:
        assert await jobs.try_job_lock(holder, name)

        assert await job(other, now=NOW) is None  # type: ignore[misc]
        await other.rollback()

        await holder.commit()  # releases the transaction-scoped lock
        assert await job(other, now=NOW) == 0  # type: ignore[misc]
        await other.commit()


async def test_job_locks_do_not_collide_with_user_locks(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    """User locks use the single-key form; job locks the two-key form. Postgres keeps
    the two key spaces apart, so even a user whose id hashes like a job name does not
    block the job."""
    async with job_factory() as holder, job_factory() as other:
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": jobs.Job.QUEUE_REMINDERS.value},
        )

        assert await jobs.try_job_lock(other, jobs.Job.QUEUE_REMINDERS)
        await other.rollback()
        await holder.rollback()


async def test_concurrent_reminder_runs_queue_one_reminder(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with job_factory() as db:
        student = await add_user(db, "student@example.edu")
        server = await add_server(db, await add_lab(db))
        await book(db, student, server, SATURDAY, now=NOW)

    results = await run_together(
        job_factory, 4, lambda session, _: jobs.queue_reminders(session, now=SATURDAY - H)
    )

    assert sorted(r for r in results if isinstance(r, int)) == [1]
    assert results.count(None) == 3
    async with job_factory() as db:
        assert await _count(db, Notification) == 1


async def test_admins_are_reminded_too(db_session: AsyncSession, server: Server) -> None:
    admin = await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)
    await book(db_session, admin, server, SATURDAY, now=NOW)

    assert await jobs.queue_reminders(db_session, now=SATURDAY - H) == 1
