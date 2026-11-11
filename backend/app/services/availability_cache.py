"""Cached GPU holds: the active allocations of each server, per UTC day, in Redis.

Availability and slot search both start from the same data: the active allocations on
a server's active GPUs that overlap a window. This module serves that data from Redis
when it can and from Postgres when it must.

Keys:
- `availability:v1:<server>:generation` is a counter, incremented after every committed
  change to that server's allocations or GPU rows. It has no TTL (one small key per
  server); if it expired, the counter would restart and could reuse a number whose
  entries are still alive.
- `availability:v1:<server>:<generation>:<YYYY-MM-DD>` holds that UTC day's
  allocations, read and written whole, with a TTL.
The `v1` is the entry format. Changing the format changes the prefix, so old entries
are never decoded by new code; they expire on their own.

Read order (see `AvailabilityCache.holds`):
1. Read each server's generation.
2. Read the day entries under those generations.
3. Load the missing days from Postgres.
4. Store them under the generations read in step 1, never under a newer one.

Write order: a service that changes a server's allocations or GPU rows calls
`mark_changed(session, server_id)`. When that session's transaction commits, an
after_commit hook increments each marked server's generation, before `commit()` returns.

Why this cannot serve a stale entry under the current generation: a reader that saw
generation G+1 in step 1 read it after the writer's increment, which follows the
writer's commit, so its query in step 3 (a new snapshot under READ COMMITTED) sees the
change. A reader that saw G may have queried before the commit; its entries go under
G, which no reader looks at once G+1 exists. Reading the generation after the query
instead would let that pre-commit result be stored under G+1.

The cache is skipped, never trusted, when Redis fails: reads fall back to Postgres. If
an increment is lost, entries can be stale until their TTL ends. Bookings never rely on
the cache; `create_reservation` checks free GPUs in the database.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.util import await_

from app.core.config import Settings
from app.models import Gpu, GpuAllocation
from app.services.booking_rules import Interval

logger = logging.getLogger(__name__)

ENTRY_FORMAT_VERSION: Final = 1
KEY_PREFIX: Final = f"availability:v{ENTRY_FORMAT_VERSION}"
DAY: Final = timedelta(days=1)
# A slow Redis must not make availability slower than the database; past this, the
# cache gives up for the request.
REDIS_TIMEOUT_SECONDS: Final = 0.5
# The increment runs inside commit(), after the data is committed, so it holds up the
# response of every write. It gets a tighter limit than reads; if Redis misses it, the
# TTL bounds how long entries stay stale.
INVALIDATION_TIMEOUT_SECONDS: Final = 0.15
_REDIS_ERRORS: Final = (RedisError, OSError, TimeoutError)

# Hit and miss counts per day entry, kept only when stats are on (load tests). One
# hash, no TTL; `python -m app.loadtest` reads and clears it.
STATS_KEY: Final = f"{KEY_PREFIX}:stats"

# Keys in Session.info. The cache is placed there by the session factory
# (app.db.session.create_session_factory); the changed servers by mark_changed.
CACHE_INFO_KEY: Final = "availability_cache"
CHANGED_INFO_KEY: Final = "availability_changed_servers"


@dataclass(frozen=True)
class Hold:
    """An active allocation on an active GPU: one GPU held for [starts_at, ends_at)."""

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime

    def overlaps(self, window: Interval) -> bool:
        return self.starts_at < window.end and self.ends_at > window.start


Loader = Callable[
    [AsyncSession, Sequence[uuid.UUID], Interval], Awaitable[dict[uuid.UUID, list[Hold]]]
]


# --- pure helpers ---


def days_covering(window: Interval) -> list[date]:
    """The UTC days that [start, end) touches. An end at midnight does not touch the
    day it starts."""
    first = window.start.astimezone(UTC).date()
    last = (window.end.astimezone(UTC) - timedelta(microseconds=1)).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def day_window(day: date) -> Interval:
    start = datetime.combine(day, time(), UTC)
    return Interval(start, start + DAY)


def split_by_day(holds: Iterable[Hold], days: Iterable[date]) -> dict[date, list[Hold]]:
    """Each day's holds. A hold that spans midnight is in every day it touches."""
    holds = list(holds)
    return {day: [h for h in holds if h.overlaps(day_window(day))] for day in days}


def merge(per_day: Iterable[Iterable[Hold]], window: Interval) -> list[Hold]:
    """The holds overlapping `window`, each once.

    A hold that spans several days is stored in each of them. Counting it twice would
    count one GPU as two busy GPUs, so holds are deduplicated by allocation id.
    """
    unique: dict[uuid.UUID, Hold] = {}
    for holds in per_day:
        for hold in holds:
            if hold.overlaps(window):
                unique.setdefault(hold.id, hold)
    return sorted(unique.values(), key=lambda h: (h.starts_at, h.ends_at, h.id))


def encode(holds: Sequence[Hold]) -> str:
    return json.dumps(
        [[str(h.id), h.starts_at.isoformat(), h.ends_at.isoformat()] for h in holds],
        separators=(",", ":"),
    )


def decode(raw: bytes | str) -> list[Hold]:
    return [
        Hold(uuid.UUID(id_), datetime.fromisoformat(start), datetime.fromisoformat(end))
        for id_, start, end in json.loads(raw)
    ]


def generation_key(server_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}:{server_id}:generation"


def day_key(server_id: uuid.UUID, generation: int, day: date) -> str:
    return f"{KEY_PREFIX}:{server_id}:{generation}:{day.isoformat()}"


# --- the database side ---


async def load_holds(
    session: AsyncSession, server_ids: Sequence[uuid.UUID], window: Interval
) -> dict[uuid.UUID, list[Hold]]:
    """Active allocations on active GPUs of the servers, overlapping the window. Pending
    and approved reservations both hold their GPUs."""
    rows = await session.execute(
        select(GpuAllocation.id, Gpu.server_id, GpuAllocation.starts_at, GpuAllocation.ends_at)
        .join(Gpu, Gpu.id == GpuAllocation.gpu_id)
        .where(
            Gpu.server_id.in_(server_ids),
            Gpu.removed_at.is_(None),
            GpuAllocation.active.is_(True),
            GpuAllocation.starts_at < window.end,
            GpuAllocation.ends_at > window.start,
        )
    )
    holds: dict[uuid.UUID, list[Hold]] = {server_id: [] for server_id in server_ids}
    for row in rows:
        holds[row.server_id].append(Hold(row.id, row.starts_at, row.ends_at))
    return holds


# --- the cache ---


class AvailabilityCache:
    def __init__(self, redis: Redis, *, ttl_seconds: int, stats: bool = False) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds
        # Counting costs a Redis round trip per read, so it is off except in the
        # separate, untimed load-test run that measures the hit rate.
        self._stats = stats

    async def holds(
        self,
        session: AsyncSession,
        server_ids: Sequence[uuid.UUID],
        window: Interval,
        *,
        load: Loader = load_holds,
    ) -> dict[uuid.UUID, list[Hold]]:
        """The holds of each server overlapping `window`, from Redis where present.

        `load` reads Postgres; tests replace it to stop a read between its steps.
        """
        days = days_covering(window)
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                # Step 1 must come before step 3; see the module docstring.
                generations = await self._generations(server_ids)
                cached = await self._read_days(server_ids, generations, days)
        except _REDIS_ERRORS:
            logger.warning("Availability cache unavailable; reading Postgres", exc_info=True)
            return await load(session, server_ids, window)

        missing = [key for key, holds in cached.items() if holds is None]
        if self._stats:
            await self._count(hits=len(cached) - len(missing), misses=len(missing))
        if missing:
            servers = sorted({server_id for server_id, _ in missing})
            missing_days = sorted({day for _, day in missing})
            span = Interval(day_window(missing_days[0]).start, day_window(missing_days[-1]).end)
            loaded = await load(session, servers, span)
            fills: dict[str, str] = {}
            for server_id, day in missing:
                day_holds = [h for h in loaded[server_id] if h.overlaps(day_window(day))]
                cached[server_id, day] = day_holds
                fills[day_key(server_id, generations[server_id], day)] = encode(day_holds)
            await self._store(fills)

        return {
            server_id: merge((cached[server_id, day] or [] for day in days), window)
            for server_id in server_ids
        }

    async def bump(self, server_ids: Collection[uuid.UUID]) -> None:
        """Move each server to a new generation, so no earlier entry is read again.

        Never raises: it runs after a commit, and the write it follows has happened.
        Cancellation (a BaseException) still propagates, since the task is being torn
        down and nobody is waiting for its answer.
        """
        try:
            async with asyncio.timeout(INVALIDATION_TIMEOUT_SECONDS):
                async with self._redis.pipeline(transaction=False) as pipe:
                    for server_id in sorted(server_ids):
                        pipe.incr(generation_key(server_id))
                    await pipe.execute()
        except Exception:
            logger.warning(
                "Could not invalidate cached availability of %d servers; entries expire "
                "within %d s",
                len(server_ids),
                self._ttl_seconds,
                exc_info=True,
            )

    async def _generations(self, server_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
        values = await self._redis.mget([generation_key(s) for s in server_ids])
        return {
            server_id: int(value) if value is not None else 0
            for server_id, value in zip(server_ids, values, strict=True)
        }

    async def _read_days(
        self,
        server_ids: Sequence[uuid.UUID],
        generations: dict[uuid.UUID, int],
        days: Sequence[date],
    ) -> dict[tuple[uuid.UUID, date], list[Hold] | None]:
        wanted = [(server_id, day) for server_id in server_ids for day in days]
        values = await self._redis.mget(
            [day_key(server_id, generations[server_id], day) for server_id, day in wanted]
        )
        result: dict[tuple[uuid.UUID, date], list[Hold] | None] = {}
        for key, value in zip(wanted, values, strict=True):
            try:
                result[key] = decode(value) if value is not None else None
            except (ValueError, TypeError):
                # Unreadable entry: treat it as missing; the fill overwrites it.
                result[key] = None
        return result

    async def _count(self, *, hits: int, misses: int) -> None:
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                async with self._redis.pipeline(transaction=False) as pipe:
                    pipe.hincrby(STATS_KEY, "hits", hits)
                    pipe.hincrby(STATS_KEY, "misses", misses)
                    await pipe.execute()
        except _REDIS_ERRORS:
            logger.warning("Could not count cache hits", exc_info=True)

    async def _store(self, fills: dict[str, str]) -> None:
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                async with self._redis.pipeline(transaction=False) as pipe:
                    for key, value in fills.items():
                        pipe.set(key, value, ex=self._ttl_seconds)
                    await pipe.execute()
        except _REDIS_ERRORS:
            logger.warning("Could not store cached availability", exc_info=True)


def build_availability_cache(settings: Settings, redis: Redis) -> AvailabilityCache | None:
    if not settings.availability_cache_enabled:
        return None
    return AvailabilityCache(
        redis,
        ttl_seconds=settings.availability_cache_ttl_seconds,
        stats=settings.availability_cache_stats,
    )


# --- what callers use ---


def mark_changed(session: AsyncSession, server_id: uuid.UUID) -> None:
    """Record that this transaction changed the server's allocations or GPU rows. Its
    cached availability is invalidated when the transaction commits."""
    session.info.setdefault(CHANGED_INFO_KEY, set()).add(server_id)


async def busy_intervals(
    session: AsyncSession, server_ids: Sequence[uuid.UUID], window: Interval
) -> dict[uuid.UUID, list[Interval]]:
    """Busy GPU intervals per server overlapping `window`: one interval per held GPU.

    Servers this session has changed but not committed are read from Postgres and not
    stored, since the session sees its own uncommitted rows: storing them would publish
    a change that may still roll back.
    """
    cache: AvailabilityCache | None = session.info.get(CACHE_INFO_KEY)
    changed: set[uuid.UUID] = session.info.get(CHANGED_INFO_KEY, set())
    direct = [s for s in server_ids if cache is None or s in changed]
    through_cache = [s for s in server_ids if s not in direct]

    holds: dict[uuid.UUID, list[Hold]] = {}
    if direct:
        holds |= await load_holds(session, direct, window)
    if through_cache and cache is not None:
        holds |= await cache.holds(session, through_cache, window)
    return {
        server_id: [Interval(h.starts_at, h.ends_at) for h in holds[server_id]]
        for server_id in server_ids
    }


# --- invalidation on commit ---


@event.listens_for(Session, "after_commit")
def _invalidate_after_commit(session: Session) -> None:
    """Increment the generation of every server the committed transaction changed.

    This runs inside AsyncSession.commit(), in the greenlet that SQLAlchemy uses to run
    the synchronous session, so `await_` can wait for Redis there. commit() returns only
    after the increment, so a client that sees a write succeed never reads availability
    from before it. Every session from create_session_factory gets this: API requests,
    assistant tools, and background jobs.

    SQLAlchemy also fires after_commit when a savepoint is released. That is not a
    commit other connections can see, so marks stay until the outermost commit;
    bumping there would let a reader store pre-commit data under the new generation.
    """
    if session.in_nested_transaction():
        return
    changed: set[uuid.UUID] | None = session.info.pop(CHANGED_INFO_KEY, None)
    cache: AvailabilityCache | None = session.info.get(CACHE_INFO_KEY)
    if not changed or cache is None:
        return
    try:
        await_(cache.bump(changed))
    except Exception:
        # The data is committed; failing here would report an error for a write that
        # happened. bump() already absorbs Redis errors; this catches the rest (for
        # example a commit outside a greenlet). Entries expire by TTL.
        logger.exception("Could not invalidate cached availability after commit")


@event.listens_for(Session, "after_transaction_end")
def _forget_after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """Drop marks left by a transaction that ended without committing (rolled back or
    closed). Savepoints are ignored: their marks belong to the enclosing transaction.
    Marks made inside a savepoint that rolls back are kept, which can only cause an
    extra invalidation."""
    if transaction.parent is None:
        session.info.pop(CHANGED_INFO_KEY, None)
