"""The allocation retry loop: which database errors are retried, and what is left behind."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AllocationContention
from app.models import Gpu, GpuAllocation, Reservation, Server, User
from app.services import reservations
from app.services.booking_rules import Interval
from app.services.reservations import NewReservation, create_reservation
from tests.integration.reservation_helpers import add_lab, add_server, add_user

pytestmark = pytest.mark.integration

# Monday 7 January 2030, 10:00 in Los Angeles; the booking is that Saturday morning.
NOW = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
START = datetime(2030, 1, 12, 18, 0, tzinfo=UTC)

SelectFree = Callable[[AsyncSession, uuid.UUID, Interval], Awaitable[list[Gpu]]]


@pytest.fixture
async def world(db_session: AsyncSession) -> tuple[User, Server, list[Gpu]]:
    """A 2-GPU server whose GPU 0 is already booked for the test window."""
    owner = await add_user(db_session, "owner@example.edu")
    user = await add_user(db_session, "student@example.edu")
    server = await add_server(db_session, await add_lab(db_session), gpu_count=2)
    await create_reservation(db_session, owner, _request(server), now=NOW)
    await db_session.commit()
    gpus = list(
        await db_session.scalars(select(Gpu).where(Gpu.server_id == server.id).order_by(Gpu.index))
    )
    return user, server, gpus


def _request(server: Server) -> NewReservation:
    return NewReservation(
        server_id=server.id,
        gpu_count=1,
        starts_at=START,
        ends_at=START + timedelta(hours=2),
        purpose="Training",
    )


def _counting(
    monkeypatch: pytest.MonkeyPatch, first: Callable[..., Awaitable[list[Gpu]]] | None
) -> list[int]:
    """Replace _select_free_gpus: the first call runs `first`, later calls the real one.
    Returns a list whose length is the number of calls."""
    real: SelectFree = reservations._select_free_gpus
    calls: list[int] = []

    async def wrapper(session: AsyncSession, server_id: uuid.UUID, interval: Interval) -> Any:
        calls.append(1)
        if first is not None and len(calls) == 1:
            return await first(session, server_id, interval)
        return await real(session, server_id, interval)

    monkeypatch.setattr(reservations, "_select_free_gpus", wrapper)
    return calls


async def _count(db: AsyncSession, model: type[Any]) -> int:
    return await db.scalar(select(func.count()).select_from(model)) or 0


async def test_exclusion_violation_is_retried(
    db_session: AsyncSession,
    world: tuple[User, Server, list[Gpu]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale read picks the booked GPU 0; the constraint rejects it and the second
    attempt, with fresh availability, takes GPU 1."""
    user, server, gpus = world

    async def stale(*_: object) -> list[Gpu]:
        return [gpus[0]]

    calls = _counting(monkeypatch, stale)

    created = await create_reservation(db_session, user, _request(server), now=NOW)
    await db_session.commit()

    assert len(calls) == 2
    assert [a.gpu.index for a in created.allocations] == [1]
    # The rolled-back attempt left no rows.
    assert await _count(db_session, Reservation) == 2
    assert await _count(db_session, GpuAllocation) == 2


async def test_deadlock_is_retried(
    db_session: AsyncSession,
    world: tuple[User, Server, list[Gpu]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, server, _ = world

    class DeadlockDetected(Exception):
        sqlstate = "40P01"

    async def deadlock(*_: object) -> list[Gpu]:
        raise DBAPIError("INSERT", None, DeadlockDetected())

    calls = _counting(monkeypatch, deadlock)

    await create_reservation(db_session, user, _request(server), now=NOW)

    assert len(calls) == 2


async def test_retries_stop_after_three_retries(
    db_session: AsyncSession,
    world: tuple[User, Server, list[Gpu]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, server, gpus = world
    calls: list[int] = []

    async def always_stale(*_: object) -> list[Gpu]:
        calls.append(1)
        return [gpus[0]]

    monkeypatch.setattr(reservations, "_select_free_gpus", always_stale)

    with pytest.raises(AllocationContention):
        await create_reservation(db_session, user, _request(server), now=NOW)

    # The first attempt plus three retries.
    assert len(calls) == 4
    await db_session.rollback()
    assert await _count(db_session, Reservation) == 1


async def test_other_integrity_errors_are_not_retried(
    db_session: AsyncSession,
    world: tuple[User, Server, list[Gpu]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign key violation (23503) is a bug, not contention: it propagates at once."""
    user, server, _ = world

    async def missing_gpu(*_: object) -> list[Gpu]:
        return [Gpu(id=uuid.uuid4(), server_id=server.id, index=0)]

    calls = _counting(monkeypatch, missing_gpu)

    with pytest.raises(IntegrityError) as caught:
        await create_reservation(db_session, user, _request(server), now=NOW)

    assert reservations.sqlstate(caught.value) == "23503"
    assert len(calls) == 1
