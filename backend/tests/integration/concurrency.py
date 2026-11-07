"""Helpers for tests that race real transactions against each other.

Every concurrent participant uses its own session and connection from an engine whose
pool is large enough for all of them, and all participants are released together by a
barrier. Every wait has a timeout, so a test whose protection is removed fails instead
of hanging.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

BARRIER_TIMEOUT: Final = 10.0
LOCK_WAIT_TIMEOUT: Final = 5.0
POLL_INTERVAL: Final = 0.05


class Gate:
    """An asyncio.Barrier whose waits time out."""

    def __init__(self, parties: int) -> None:
        self._barrier = asyncio.Barrier(parties)

    async def wait(self) -> None:
        await asyncio.wait_for(self._barrier.wait(), BARRIER_TIMEOUT)


async def run_together[T](
    factory: async_sessionmaker[AsyncSession],
    count: int,
    call: Callable[[AsyncSession, int], Awaitable[T]],
) -> list[T | BaseException]:
    """Run `call(session, i)` for i in range(count), each in its own session.

    Each participant first checks out its connection, then waits at the barrier, so
    connection setup does not stagger the start. The participant commits on success.
    Exceptions are returned, not raised.
    """
    gate = Gate(count)

    async def one(i: int) -> T:
        async with factory() as session:
            await session.connection()
            await gate.wait()
            result = await call(session, i)
            await session.commit()
            return result

    return await asyncio.gather(*(one(i) for i in range(count)), return_exceptions=True)


async def backend_pids_waiting_on_locks(engine: AsyncEngine, pids: list[int]) -> list[int]:
    async with engine.connect() as conn:
        rows = await conn.scalars(
            text(
                "SELECT pid FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND pid = ANY(:pids)"
            ),
            {"pids": pids},
        )
        return list(rows)


async def wait_until_blocked(
    engine: AsyncEngine, pid: int, *, unless_done: "asyncio.Future[Any] | None" = None
) -> bool:
    """Poll until the backend `pid` waits on a lock. False if it never does within
    LOCK_WAIT_TIMEOUT, or if `unless_done` finishes first (it ran without waiting)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_WAIT_TIMEOUT
    while loop.time() < deadline:
        if unless_done is not None and unless_done.done():
            return False
        if await backend_pids_waiting_on_locks(engine, [pid]):
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def backend_pid(session: AsyncSession) -> int:
    pid = await session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(pid, int)
    return pid


@dataclass(frozen=True)
class PairOutcome[T]:
    """How the second transaction of a pair fared: whether it waited on a lock held by
    the first, and what it returned or raised."""

    blocked: bool
    result: T | None
    error: Exception | None


async def race_pair[T](
    engine: AsyncEngine,
    first: AsyncSession,
    second: AsyncSession,
    second_call: Callable[[], Awaitable[T]],
) -> PairOutcome[T]:
    """`first` has done its work and holds its locks, uncommitted. Start `second_call`
    in `second`, record whether it blocks on a lock, then commit `first` and collect
    the outcome of `second`, committing it on success.

    The second call is always awaited before returning, so no query is left running
    on a session that is about to close.
    """
    pid = await backend_pid(second)
    task = asyncio.create_task(second_call())
    try:
        blocked = await wait_until_blocked(engine, pid, unless_done=task)
        await first.commit()
    except BaseException:
        await first.rollback()
        raise
    finally:
        await asyncio.wait({task}, timeout=LOCK_WAIT_TIMEOUT)
        if not task.done():
            task.cancel()
    error = task.exception()
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        await second.rollback()
        return PairOutcome(blocked=blocked, result=None, error=error)
    await second.commit()
    return PairOutcome(blocked=blocked, result=task.result(), error=None)
