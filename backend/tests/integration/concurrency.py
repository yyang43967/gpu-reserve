"""Helpers for tests that race real transactions against each other.

Every concurrent participant uses its own session and connection from an engine whose
pool is large enough for all of them, and all participants are released together by a
barrier. Every wait has a timeout, so a test whose protection is removed fails instead
of hanging.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final

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


async def wait_until_blocked(engine: AsyncEngine, pid: int) -> bool:
    """Poll until the backend `pid` waits on a lock. False if it never does within
    LOCK_WAIT_TIMEOUT."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_WAIT_TIMEOUT
    while loop.time() < deadline:
        if await backend_pids_waiting_on_locks(engine, [pid]):
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


async def backend_pid(session: AsyncSession) -> int:
    pid = await session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(pid, int)
    return pid
