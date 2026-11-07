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
from sqlalchemy.orm import contains_eager

from app.core.config import Settings
from app.core.exceptions import (
    AllocationContention,
    GpusInUse,
    GpuUnavailable,
    InvalidReservationState,
    InvalidToken,
    QuotaExceeded,
)
from app.models import GpuAllocation, Reservation, ReservationStatus, Server, User, UserRole
from app.models.reservation import ALLOCATION_OVERLAP_CONSTRAINT
from app.services import reservations, servers, users
from app.services.booking_rules import Interval
from app.services.reservations import NewReservation, create_reservation
from tests.integration.concurrency import (
    LOCK_WAIT_TIMEOUT,
    Gate,
    PairOutcome,
    race_pair,
    run_together,
)
from tests.integration.db_utils import truncate_all
from tests.integration.reservation_helpers import QUOTA_ZONE, add_lab, add_server, add_user

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
        await create_reservation(
            db, owner, _one_gpu(server, gpus=2), now=NOW, quota_zone=QUOTA_ZONE
        )
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
        return await create_reservation(
            session, students[i], _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
        )

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
        return await create_reservation(
            session, students[i], _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
        )

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
        return await create_reservation(
            session, student, _thirteen_hours(servers[i]), now=NOW, quota_zone=QUOTA_ZONE
        )

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
        return await create_reservation(
            session, student, _thirteen_hours(servers[i]), now=NOW, quota_zone=QUOTA_ZONE
        )

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
        return await create_reservation(
            session, students[i], _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
        )

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
    student, servers_ = await _quota_race(factory)
    async with factory() as db:
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)

    async with factory() as admin_session, factory() as booking_session:
        await users.update_user(admin_session, admin, student.id, weekly_gpu_hour_quota=0, now=NOW)
        outcome = await race_pair(
            race_engine,
            admin_session,
            booking_session,
            lambda: create_reservation(
                booking_session,
                student,
                _thirteen_hours(servers_[0]),
                now=NOW,
                quota_zone=QUOTA_ZONE,
            ),
        )

    assert outcome.blocked
    assert isinstance(outcome.error, QuotaExceeded)


# --- 3. booking vs a gpu_count reduction ---


async def _reduction_race(factory: async_sessionmaker[AsyncSession]) -> tuple[Server, User, User]:
    """A 4-GPU server whose GPUs 0 and 1 are booked by someone else for the window, so
    the only free GPUs are 2 and 3: exactly the ones a reduction to 2 removes. Without
    this, lowest-k would pick a surviving GPU and the race could not show."""
    async with factory() as db:
        server = await add_server(db, await add_lab(db), gpu_count=4)
        owner = await add_user(db, "owner@example.edu")
        await create_reservation(
            db, owner, _one_gpu(server, gpus=2), now=NOW, quota_zone=QUOTA_ZONE
        )
        await db.commit()
        student = await add_user(db, "student@example.edu")
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)
    return server, student, admin


async def _active_allocations_on_removed_gpus(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM gpu_allocations a JOIN gpus g ON g.id = a.gpu_id "
                "WHERE a.active AND g.removed_at IS NOT NULL"
            )
        )
    return count or 0


async def _lock_server_without_share(
    session: AsyncSession, viewer: User, server_id: uuid.UUID
) -> Server:
    """_lock_server_shared without FOR SHARE: the protection the controls remove."""
    server = await session.scalar(
        select(Server)
        .join(Server.lab)
        .where(Server.id == server_id)
        .options(contains_eager(Server.lab))
        .execution_options(populate_existing=True)
    )
    assert server is not None
    return server


async def _reduction_first(
    factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> PairOutcome[Reservation]:
    """The reduction holds the server lock, uncommitted, while the student books."""
    server, student, _admin = await _reduction_race(factory)
    async with factory() as admin_session, factory() as booking_session:
        await servers.update_server(admin_session, server.id, gpu_count=2, now=NOW)
        return await race_pair(
            engine,
            admin_session,
            booking_session,
            lambda: create_reservation(
                booking_session, student, _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
            ),
        )


async def _booking_first(
    factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> PairOutcome[Server]:
    """The booking holds its locks and its new allocation, uncommitted, while an admin
    reduces gpu_count."""
    server, student, _admin = await _reduction_race(factory)
    async with factory() as booking_session, factory() as admin_session:
        await create_reservation(
            booking_session, student, _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
        )
        return await race_pair(
            engine,
            booking_session,
            admin_session,
            lambda: servers.update_server(admin_session, server.id, gpu_count=2, now=NOW),
        )


async def test_booking_waits_for_a_reduction_in_progress(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    """FOR SHARE makes the booking wait before it reads which GPUs are free, so it sees
    the committed reduction."""
    outcome = await _reduction_first(factory, race_engine)

    assert outcome.blocked
    assert isinstance(outcome.error, GpuUnavailable)
    assert await _active_allocations_on_removed_gpus(race_engine) == 0


async def test_control_reduction_first_without_for_share(
    factory: async_sessionmaker[AsyncSession],
    race_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control. Without FOR SHARE the booking reads the GPUs while the removal
    is uncommitted and still sees GPU 2 as active. It does block later, when inserting
    the reservation (the foreign key to servers takes KEY SHARE, which conflicts with
    the admin's FOR UPDATE), but by then its choice is made: once the admin commits it
    inserts an allocation on the removed GPU."""
    monkeypatch.setattr(reservations, "_lock_server_shared", _lock_server_without_share)

    outcome = await _reduction_first(factory, race_engine)

    assert outcome.error is None
    assert await _active_allocations_on_removed_gpus(race_engine) == 1


async def test_reduction_waits_for_a_booking_in_progress(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    outcome = await _booking_first(factory, race_engine)

    assert outcome.blocked
    assert isinstance(outcome.error, GpusInUse)
    assert await _active_allocations_on_removed_gpus(race_engine) == 0


async def test_booking_first_is_also_covered_by_the_reservation_foreign_key(
    factory: async_sessionmaker[AsyncSession],
    race_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In this order FOR SHARE is not the only protection, so no negative control can
    show a race by removing it. The booking's insert into reservations already holds
    KEY SHARE on the server row (foreign key check), and the reduction's FOR UPDATE
    waits for it either way. This test pins that behavior down."""
    monkeypatch.setattr(reservations, "_lock_server_shared", _lock_server_without_share)

    outcome = await _booking_first(factory, race_engine)

    assert outcome.blocked
    assert isinstance(outcome.error, GpusInUse)


# --- 4. booking vs other server edits ---


async def test_server_edits_wait_for_bookings_in_progress(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    """Every server change takes FOR UPDATE, not only gpu_count changes, so an edit to
    max_booking_hours waits until a booking that read the old limit commits."""
    server, student, _ = await _reduction_race(factory)
    async with factory() as booking_session, factory() as admin_session:
        await create_reservation(
            booking_session, student, _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
        )
        outcome = await race_pair(
            race_engine,
            booking_session,
            admin_session,
            lambda: servers.update_server(admin_session, server.id, max_booking_hours=1, now=NOW),
        )

    assert outcome.blocked
    assert outcome.result is not None and outcome.result.max_booking_hours == 1


# --- deactivation vs booking ---


async def test_booking_waits_for_a_deactivation_and_is_refused(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    server, student, admin = await _reduction_race(factory)

    async with factory() as admin_session, factory() as booking_session:
        await users.update_user(admin_session, admin, student.id, is_active=False, now=NOW)
        outcome = await race_pair(
            race_engine,
            admin_session,
            booking_session,
            lambda: create_reservation(
                booking_session, student, _one_gpu(server), now=NOW, quota_zone=QUOTA_ZONE
            ),
        )

    assert outcome.blocked
    assert isinstance(outcome.error, InvalidToken)


# --- 5. concurrent transitions on one reservation ---


async def _pending_reservation(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[Reservation, User, User]:
    async with factory() as db:
        lab = await add_lab(db)
        restricted = await add_server(db, lab, "h100-node-01", requires_approval=True)
        student = await add_user(db, "student@example.edu")
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)
        pending = await create_reservation(
            db, student, _one_gpu(restricted), now=NOW, quota_zone=QUOTA_ZONE
        )
        await db.commit()
    return pending, student, admin


async def _final_state(
    factory: async_sessionmaker[AsyncSession], reservation_id: uuid.UUID
) -> tuple[ReservationStatus, list[bool]]:
    async with factory() as db:
        status = await db.scalar(select(Reservation.status).where(Reservation.id == reservation_id))
        active = list(
            await db.scalars(
                select(GpuAllocation.active).where(GpuAllocation.reservation_id == reservation_id)
            )
        )
    assert status is not None
    return status, active


def _decide(admin: User, reservation_id: uuid.UUID) -> Callable[[AsyncSession, int], Any]:
    async def decide(session: AsyncSession, i: int) -> Reservation:
        if i == 0:
            return await reservations.approve_reservation(session, admin, reservation_id, now=NOW)
        return await reservations.reject_reservation(
            session, admin, reservation_id, reason="Class booking", now=NOW
        )

    return decide


async def test_concurrent_approve_and_reject_one_wins(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    pending, _, admin = await _pending_reservation(factory)

    results = await run_together(factory, 2, _decide(admin, pending.id))

    successes, failures = _outcomes(results)
    assert len(successes) == 1
    assert len(failures) == 1 and isinstance(failures[0], InvalidReservationState)
    status, active = await _final_state(factory, pending.id)
    assert active == [status is ReservationStatus.APPROVED]


async def test_control_without_the_row_lock_both_decisions_succeed(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: without FOR UPDATE, both transactions read `pending` and both
    write their decision. The last write wins, and an approval can end up with the
    released allocations of the rejection."""
    pending, _, admin = await _pending_reservation(factory)
    gate = Gate(2)

    async def read_then_wait(
        session: AsyncSession, viewer: User, reservation_id: uuid.UUID
    ) -> Reservation:
        reservation = await session.scalar(
            select(Reservation)
            .where(Reservation.id == reservation_id)
            .execution_options(populate_existing=True)
        )
        assert reservation is not None
        await gate.wait()
        return reservation

    monkeypatch.setattr(reservations, "_lock_reservation", read_then_wait)

    results = await run_together(factory, 2, _decide(admin, pending.id))

    successes, _ = _outcomes(results)
    assert len(successes) == 2


async def test_concurrent_approve_and_cancel_stay_consistent(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Approve then cancel both succeed (cancelled); cancel then approve refuses the
    approval. Either way the allocations match the final status."""
    pending, student, admin = await _pending_reservation(factory)

    async def act(session: AsyncSession, i: int) -> Reservation:
        if i == 0:
            return await reservations.approve_reservation(session, admin, pending.id, now=NOW)
        return await reservations.cancel_reservation(session, student, pending.id, now=NOW)

    results = await run_together(factory, 2, act)

    _, failures = _outcomes(results)
    assert all(isinstance(f, InvalidReservationState) for f in failures)
    status, active = await _final_state(factory, pending.id)
    assert status is ReservationStatus.CANCELLED
    assert active == [False]


async def test_approve_waits_for_a_deactivation_and_is_refused(
    factory: async_sessionmaker[AsyncSession], race_engine: AsyncEngine
) -> None:
    """Deactivation locks the user, then the user's reservation rows. An approval of one
    of them waits for the row lock and then sees it cancelled."""
    pending, student, admin = await _pending_reservation(factory)

    async with factory() as admin_session, factory() as approve_session:
        await users.update_user(admin_session, admin, student.id, is_active=False, now=NOW)
        outcome = await race_pair(
            race_engine,
            admin_session,
            approve_session,
            lambda: reservations.approve_reservation(approve_session, admin, pending.id, now=NOW),
        )

    assert outcome.blocked
    assert isinstance(outcome.error, InvalidReservationState)
    assert await _final_state(factory, pending.id) == (ReservationStatus.CANCELLED, [False])


async def test_control_approve_and_cancel_without_the_row_lock_lose_an_update(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for approve vs cancel. Without FOR UPDATE, the approval reads
    `pending` and is held there while the cancel runs and commits. The approval then
    writes from its stale read: both succeed, the cancel is lost, and the reservation
    is `approved` while its allocations were released by the cancel."""
    pending, student, admin = await _pending_reservation(factory)
    approval_has_read = asyncio.Event()
    cancel_committed = asyncio.Event()

    async def read_without_lock(
        session: AsyncSession, viewer: User, reservation_id: uuid.UUID
    ) -> Reservation:
        reservation = await session.scalar(
            select(Reservation)
            .where(Reservation.id == reservation_id)
            .execution_options(populate_existing=True)
        )
        assert reservation is not None
        if viewer.id == admin.id:
            approval_has_read.set()
            await asyncio.wait_for(cancel_committed.wait(), LOCK_WAIT_TIMEOUT)
        return reservation

    monkeypatch.setattr(reservations, "_lock_reservation", read_without_lock)

    async def approve() -> Reservation:
        async with factory() as session:
            approved = await reservations.approve_reservation(session, admin, pending.id, now=NOW)
            await session.commit()
            return approved

    approval = asyncio.create_task(approve())
    await asyncio.wait_for(approval_has_read.wait(), LOCK_WAIT_TIMEOUT)
    async with factory() as session:
        await reservations.cancel_reservation(session, student, pending.id, now=NOW)
        await session.commit()
    cancel_committed.set()
    await asyncio.wait_for(approval, LOCK_WAIT_TIMEOUT)

    assert await _final_state(factory, pending.id) == (ReservationStatus.APPROVED, [False])
