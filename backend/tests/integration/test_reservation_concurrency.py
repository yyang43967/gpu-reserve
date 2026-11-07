"""Races between real transactions on Postgres.

Each test has a paired negative control that removes the protection under test and
shows the race actually happens. The controls hold every participant at the race
window with a barrier, so they fail deterministically rather than by chance.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.exceptions import AllocationContention, GpuUnavailable, QuotaExceeded
from app.models import GpuAllocation, Reservation, Server, User, UserRole
from app.models.reservation import ALLOCATION_OVERLAP_CONSTRAINT
from app.services import reservations, users
from app.services.booking_rules import Interval
from app.services.reservations import NewReservation, create_reservation
from tests.integration.concurrency import (
    LOCK_WAIT_TIMEOUT,
    Gate,
    backend_pid,
    run_together,
    wait_until_blocked,
)
from tests.integration.db_utils import truncate_all
from tests.integration.reservation_helpers import add_lab, add_server, add_user

pytestmark = pytest.mark.integration

BOOKERS: Final = 50
QUOTA_RACERS: Final = 10
# Monday 7 January 2030, 10:00 in Los Angeles.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
# Saturday 12 January 2030, 08:00 in Los Angeles: same quota week, off-peak.
SATURDAY_MORNING: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)


@pytest.fixture
async def race_engine(make_settings: Callable[..., Settings]) -> AsyncIterator[AsyncEngine]:
    """An engine with a connection for every participant. Committed data is removed
    explicitly afterwards."""
    engine = create_async_engine(
        str(make_settings().database_url), pool_size=BOOKERS + 5, max_overflow=0
    )
    yield engine
    await engine.dispose()
    await truncate_all(engine)
    await engine.dispose()


@pytest.fixture
def factory(race_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(race_engine, expire_on_commit=False)


async def _overlapping_active_pairs(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM gpu_allocations a JOIN gpu_allocations b "
                "ON a.gpu_id = b.gpu_id AND a.id < b.id "
                "AND a.active AND b.active "
                "AND tstzrange(a.starts_at, a.ends_at, '[)') "
                "&& tstzrange(b.starts_at, b.ends_at, '[)')"
            )
        )
    return count or 0


def _outcomes(results: list[Any]) -> tuple[list[Reservation], list[BaseException]]:
    successes = [r for r in results if isinstance(r, Reservation)]
    failures = [r for r in results if isinstance(r, BaseException)]
    return successes, failures


# --- 1. the last free GPUs ---


async def _last_two_gpus(factory: async_sessionmaker[AsyncSession]) -> tuple[Server, list[User]]:
    """A 4-GPU server with GPUs 0 and 1 booked, and 50 students who each want one GPU
    for the same window. Distinct students, so neither the per-user lock nor the
    active-reservation limit plays a part."""
    async with factory() as db:
        server = await add_server(db, await add_lab(db), gpu_count=4)
        owner = await add_user(db, "owner@example.edu")
        await create_reservation(db, owner, _one_gpu(server, gpus=2), now=NOW)
        await db.commit()
        students = [await add_user(db, f"student{i:02}@example.edu") for i in range(BOOKERS)]
    return server, students


def _one_gpu(server: Server, *, gpus: int = 1) -> NewReservation:
    return NewReservation(
        server_id=server.id,
        gpu_count=gpus,
        starts_at=SATURDAY_MORNING,
        ends_at=SATURDAY_MORNING + timedelta(hours=2),
        purpose="Training",
    )


async def test_concurrent_bookings_for_the_last_free_gpus(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    server, students = await _last_two_gpus(factory)

    async def book(session: AsyncSession, i: int) -> Reservation:
        return await create_reservation(session, students[i], _one_gpu(server), now=NOW)

    successes, failures = _outcomes(await run_together(factory, BOOKERS, book))

    assert len(successes) == 2
    assert sorted(r.allocations[0].gpu.index for r in successes) == [2, 3]
    assert len(failures) == BOOKERS - 2
    assert all(isinstance(f, GpuUnavailable | AllocationContention) for f in failures), failures
    assert await _overlapping_active_pairs(race_engine) == 0


async def test_control_without_the_exclusion_constraint_double_books(
    factory: async_sessionmaker[AsyncSession],
    race_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: with the constraint dropped and every request holding after it
    reads availability, all of them take the same GPU."""
    server, students = await _last_two_gpus(factory)
    real = reservations._select_free_gpus
    gate = Gate(BOOKERS)

    async def read_then_wait(
        session: AsyncSession, server_id: uuid.UUID, interval: Interval
    ) -> Any:
        free = await real(session, server_id, interval)
        await gate.wait()
        return free

    monkeypatch.setattr(reservations, "_select_free_gpus", read_then_wait)

    async def book(session: AsyncSession, i: int) -> Reservation:
        return await create_reservation(session, students[i], _one_gpu(server), now=NOW)

    async with race_engine.begin() as conn:
        definition = await conn.scalar(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :name"),
            {"name": ALLOCATION_OVERLAP_CONSTRAINT},
        )
        await conn.execute(
            text(f"ALTER TABLE gpu_allocations DROP CONSTRAINT {ALLOCATION_OVERLAP_CONSTRAINT}")
        )
    try:
        successes, _ = _outcomes(await run_together(factory, BOOKERS, book))
        overlaps = await _overlapping_active_pairs(race_engine)
    finally:
        await truncate_all(race_engine)
        async with race_engine.begin() as conn:
            await conn.execute(
                text(
                    f"ALTER TABLE gpu_allocations ADD CONSTRAINT "
                    f"{ALLOCATION_OVERLAP_CONSTRAINT} {definition}"
                )
            )

    assert len(successes) > 2
    assert overlaps > 0


# --- 2. the weekly quota ---


async def _quota_race(factory: async_sessionmaker[AsyncSession]) -> tuple[User, list[Server]]:
    """One student with a 48-hour quota and ten one-GPU servers. Each request books
    13 GPU-hours on its own server on the same Saturday, so GPU capacity, peak hours,
    and the four-reservation limit never bind: only the quota does (3 x 13 = 39 <= 48,
    4 x 13 = 52 > 48)."""
    async with factory() as db:
        lab = await add_lab(db)
        student = await add_user(db, "student@example.edu", quota=48)
        servers = [
            await add_server(db, lab, f"node-{i:02}", gpu_count=1, max_booking_hours=13)
            for i in range(QUOTA_RACERS)
        ]
    return student, servers


def _thirteen_hours(server: Server) -> NewReservation:
    return NewReservation(
        server_id=server.id,
        gpu_count=1,
        starts_at=SATURDAY_MORNING,
        ends_at=SATURDAY_MORNING + timedelta(hours=13),
        purpose="Training",
    )


async def _booked_gpu_hours(factory: async_sessionmaker[AsyncSession], user: User) -> float:
    async with factory() as db:
        seconds = await db.scalar(
            select(
                func.sum(
                    Reservation.gpu_count
                    * func.extract("epoch", Reservation.ends_at - Reservation.starts_at)
                )
            ).where(Reservation.user_id == user.id)
        )
    return float(seconds or 0) / 3600


async def test_concurrent_bookings_never_exceed_the_quota(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    student, servers = await _quota_race(factory)

    async def book(session: AsyncSession, i: int) -> Reservation:
        return await create_reservation(session, student, _thirteen_hours(servers[i]), now=NOW)

    successes, failures = _outcomes(await run_together(factory, QUOTA_RACERS, book))

    assert len(successes) == 3
    assert all(isinstance(f, QuotaExceeded) for f in failures), failures
    assert await _booked_gpu_hours(factory, student) == 39


async def test_control_without_the_user_lock_exceeds_the_quota(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: without the advisory lock, and with every request holding after
    it reads its usage, all of them see an empty week."""
    student, servers = await _quota_race(factory)
    real = reservations._load_quota_bookings
    gate = Gate(QUOTA_RACERS)

    async def no_lock(*_: object) -> None:
        return None

    async def read_then_wait(session: AsyncSession, user_id: uuid.UUID, span: Interval) -> Any:
        bookings = await real(session, user_id, span)
        await gate.wait()
        return bookings

    monkeypatch.setattr(reservations, "_lock_user_bookings", no_lock)
    monkeypatch.setattr(reservations, "_load_quota_bookings", read_then_wait)

    async def book(session: AsyncSession, i: int) -> Reservation:
        return await create_reservation(session, student, _thirteen_hours(servers[i]), now=NOW)

    successes, _ = _outcomes(await run_together(factory, QUOTA_RACERS, book))

    assert len(successes) > 3
    assert await _booked_gpu_hours(factory, student) > 48


async def test_reservation_rows_match_allocations(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """After a race, every reservation holds exactly gpu_count active allocations with
    its own time range."""
    server, students = await _last_two_gpus(factory)

    async def book(session: AsyncSession, i: int) -> Reservation:
        return await create_reservation(session, students[i], _one_gpu(server), now=NOW)

    await run_together(factory, BOOKERS, book)

    async with factory() as db:
        rows = await db.execute(
            select(Reservation.gpu_count, func.count(GpuAllocation.id))
            .join(GpuAllocation, GpuAllocation.reservation_id == Reservation.id)
            .where(
                GpuAllocation.active,
                GpuAllocation.starts_at == Reservation.starts_at,
                GpuAllocation.ends_at == Reservation.ends_at,
            )
            .group_by(Reservation.id)
        )
        assert all(expected == actual for expected, actual in rows)


async def test_booking_waits_for_a_quota_change_and_uses_the_new_quota(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    """An admin lowers a student's quota to zero while the student books. The booking
    waits for the change and is checked against the new quota, not the value on the
    User object it was given."""
    student, servers = await _quota_race(factory)
    async with factory() as db:
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)

    async with factory() as admin_session, factory() as booking_session:
        await users.update_user(admin_session, admin, student.id, weekly_gpu_hour_quota=0)
        booking_pid = await backend_pid(booking_session)
        booking = asyncio.create_task(
            create_reservation(booking_session, student, _thirteen_hours(servers[0]), now=NOW)
        )

        assert await wait_until_blocked(race_engine, booking_pid)
        await admin_session.commit()
        with pytest.raises(QuotaExceeded):
            await asyncio.wait_for(booking, LOCK_WAIT_TIMEOUT)
