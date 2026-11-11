"""The availability cache against real Postgres and Redis: hits, invalidation on every
write path, the read/commit race, and Redis outages."""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.security import create_access_token
from app.db.session import create_session_factory
from app.models import Reservation, Server, User, UserRole
from app.services import jobs, reservations, servers
from app.services.availability_cache import (
    CHANGED_INFO_KEY,
    AvailabilityCache,
    Hold,
    busy_intervals,
    day_key,
    generation_key,
    load_holds,
    mark_changed,
    merge,
)
from app.services.booking_rules import Interval
from tests.integration.conftest import UNREACHABLE_HOST
from tests.integration.reservation_helpers import (
    QUOTA_ZONE,
    add_lab,
    add_server,
    add_user,
    booking_body,
    next_saturday,
)
from tests.integration.test_admin_users import bearer, create_user, login

pytestmark = pytest.mark.integration

H = timedelta(hours=1)
TTL = 300
# Monday 7 January 2030, 10:00 in Los Angeles.
NOW = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
# Saturday 12 January 2030, 08:00 in Los Angeles: off-peak.
SATURDAY = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
SATURDAY_UTC_DAY = Interval(datetime(2030, 1, 12, tzinfo=UTC), datetime(2030, 1, 13, tzinfo=UTC))
WINDOW = Interval(SATURDAY_UTC_DAY.start, SATURDAY_UTC_DAY.start + timedelta(days=3))

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def cache(redis: Redis) -> AvailabilityCache:
    return AvailabilityCache(redis, ttl_seconds=TTL)


@pytest.fixture
def factory(job_engine: AsyncEngine, cache: AvailabilityCache) -> Factory:
    return create_session_factory(job_engine, availability_cache=cache)


@pytest.fixture
async def student(factory: Factory) -> User:
    async with factory() as session:
        return await add_user(session, "ada@example.edu")


@pytest.fixture
async def admin(factory: Factory) -> User:
    async with factory() as session:
        return await add_user(session, "root@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def server(factory: Factory) -> Server:
    async with factory() as session:
        return await add_server(session, await add_lab(session), gpu_count=4)


async def generation(redis: Redis, server_id: uuid.UUID) -> int:
    value = await redis.get(generation_key(server_id))
    return int(value) if value is not None else 0


async def cached(factory: Factory, server_id: uuid.UUID) -> list[Interval]:
    async with factory() as session:
        return (await busy_intervals(session, [server_id], WINDOW))[server_id]


async def in_postgres(factory: Factory, server_id: uuid.UUID) -> list[Interval]:
    async with factory() as session:
        holds = (await load_holds(session, [server_id], WINDOW))[server_id]
    return [Interval(h.starts_at, h.ends_at) for h in merge([holds], WINDOW)]


async def book(
    factory: Factory,
    user: User,
    server: Server,
    start: datetime = SATURDAY,
    *,
    hours: float = 1,
    gpus: int = 1,
    now: datetime = NOW,
) -> Reservation:
    async with factory() as session:
        reservation = await reservations.create_reservation(
            session,
            user,
            reservations.NewReservation(
                server.id, gpus, start, start + timedelta(hours=hours), "Fine-tuning"
            ),
            now=now,
            quota_zone=QUOTA_ZONE,
        )
        await session.commit()
        return reservation


class CountingLoader:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, session: AsyncSession, server_ids: Sequence[uuid.UUID], window: Interval
    ) -> dict[uuid.UUID, list[Hold]]:
        self.calls += 1
        return await load_holds(session, server_ids, window)


# --- reads ---


async def test_a_warm_read_does_not_query_postgres(
    factory: Factory, cache: AvailabilityCache, redis: Redis, student: User, server: Server
) -> None:
    await book(factory, student, server, gpus=2)
    loader = CountingLoader()

    async with factory() as session:
        cold = await cache.holds(session, [server.id], WINDOW, load=loader)
        warm = await cache.holds(session, [server.id], WINDOW, load=loader)

    assert loader.calls == 1
    assert cold == warm
    assert len(warm[server.id]) == 2
    assert await cached(factory, server.id) == await in_postgres(factory, server.id)
    key = day_key(server.id, await generation(redis, server.id), SATURDAY_UTC_DAY.start.date())
    assert 0 < await redis.ttl(key) <= TTL


async def test_a_booking_across_midnight_counts_once(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    # 22:00 to 02:00 UTC: stored in both days' entries.
    late = SATURDAY_UTC_DAY.end - 2 * H
    await book(factory, student, server, late, hours=4)

    cold = await cached(factory, server.id)
    warm = await cached(factory, server.id)

    assert cold == warm == [Interval(late, late + 4 * H)]
    gen = await generation(redis, server.id)
    for day in (SATURDAY_UTC_DAY.start.date(), SATURDAY_UTC_DAY.end.date()):
        assert await redis.exists(day_key(server.id, gen, day))


async def test_availability_runs_from_a_warm_cache(
    factory: Factory, student: User, server: Server
) -> None:
    await book(factory, student, server, SATURDAY_UTC_DAY.end - H, hours=2, gpus=3)

    async def free_runs() -> list[reservations.FreeRun]:
        async with factory() as session:
            availability = await reservations.server_availability(
                session, student, server.id, starts_at=WINDOW.start, ends_at=WINDOW.end, now=NOW
            )
            return availability.runs

    cold, warm = await free_runs(), await free_runs()
    assert cold == warm
    assert [run.free for run in warm] == [4, 1, 4]


# --- invalidation on every write path ---


Write = Callable[[AsyncSession], Awaitable[object]]


async def assert_invalidated_by(
    factory: Factory, redis: Redis, server: Server, write: Write
) -> None:
    """Warm the cache, run `write` in its own committed transaction, and check that the
    server moved to a new generation and the cache agrees with Postgres again."""
    before = await cached(factory, server.id)
    generation_before = await generation(redis, server.id)

    async with factory() as session:
        await write(session)
        await session.commit()

    assert await generation(redis, server.id) > generation_before
    after = await cached(factory, server.id)
    assert after == await in_postgres(factory, server.id)
    assert after != before


async def test_creating_a_reservation_invalidates(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    async def write(session: AsyncSession) -> object:
        return await reservations.create_reservation(
            session,
            student,
            reservations.NewReservation(server.id, 1, SATURDAY, SATURDAY + H, "Run"),
            now=NOW,
            quota_zone=QUOTA_ZONE,
        )

    await assert_invalidated_by(factory, redis, server, write)


async def test_cancelling_invalidates(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    reservation = await book(factory, student, server)

    async def write(session: AsyncSession) -> object:
        return await reservations.cancel_reservation(session, student, reservation.id, now=NOW)

    await assert_invalidated_by(factory, redis, server, write)


async def test_ending_early_invalidates(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    reservation = await book(factory, student, server, hours=4)

    async def write(session: AsyncSession) -> object:
        return await reservations.cancel_reservation(
            session, student, reservation.id, now=SATURDAY + H
        )

    await assert_invalidated_by(factory, redis, server, write)


async def test_rejecting_invalidates(
    factory: Factory, redis: Redis, student: User, admin: User
) -> None:
    async with factory() as session:
        restricted = await add_server(
            session, await add_lab(session, "NLP Lab"), "h100-node-01", requires_approval=True
        )
    reservation = await book(factory, student, restricted)

    async def write(session: AsyncSession) -> object:
        return await reservations.reject_reservation(
            session, admin, reservation.id, reason="Maintenance", now=NOW
        )

    await assert_invalidated_by(factory, redis, restricted, write)


async def test_approving_changes_nothing_cached(
    factory: Factory, redis: Redis, student: User, admin: User
) -> None:
    async with factory() as session:
        restricted = await add_server(
            session, await add_lab(session, "NLP Lab"), "h100-node-01", requires_approval=True
        )
    reservation = await book(factory, student, restricted)
    before = await cached(factory, restricted.id)
    generation_before = await generation(redis, restricted.id)

    async with factory() as session:
        await reservations.approve_reservation(session, admin, reservation.id, now=NOW)
        await session.commit()

    # Pending already held the GPUs, so nothing changed and nothing was invalidated.
    assert await generation(redis, restricted.id) == generation_before
    assert (
        await cached(factory, restricted.id) == before == await in_postgres(factory, restricted.id)
    )


async def test_releasing_a_deactivated_users_bookings_invalidates(
    factory: Factory, redis: Redis, student: User, admin: User, server: Server
) -> None:
    await book(factory, student, server)

    async def write(session: AsyncSession) -> object:
        return await reservations.release_user_reservations(
            session, student.id, now=NOW, actor_id=admin.id
        )

    await assert_invalidated_by(factory, redis, server, write)


async def test_the_expiry_job_invalidates(factory: Factory, redis: Redis, student: User) -> None:
    async with factory() as session:
        restricted = await add_server(
            session, await add_lab(session, "NLP Lab"), "h100-node-01", requires_approval=True
        )
    await book(factory, student, restricted)

    async def write(session: AsyncSession) -> object:
        assert await jobs.expire_pending(session, now=SATURDAY) == 1
        return None

    await assert_invalidated_by(factory, redis, restricted, write)


async def test_the_completion_job_invalidates(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    await book(factory, student, server)

    async def write(session: AsyncSession) -> object:
        assert await jobs.complete_finished(session, now=SATURDAY + H) == 1
        return None

    await assert_invalidated_by(factory, redis, server, write)


async def test_removing_gpus_invalidates(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    # GPU 3 is held; removing it hides its allocation. Its booking ended long ago, as far
    # as `now` is concerned, so the removal is allowed.
    await book(factory, student, server, gpus=4)

    async def write(session: AsyncSession) -> object:
        return await servers.update_server(session, server.id, gpu_count=2, now=SATURDAY + 2 * H)

    await assert_invalidated_by(factory, redis, server, write)


async def test_a_rollback_invalidates_nothing(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    before = await cached(factory, server.id)
    generation_before = await generation(redis, server.id)

    async with factory() as session:
        await reservations.create_reservation(
            session,
            student,
            reservations.NewReservation(server.id, 1, SATURDAY, SATURDAY + H, "Run"),
            now=NOW,
            quota_zone=QUOTA_ZONE,
        )
        assert server.id in session.info[CHANGED_INFO_KEY]
        await session.rollback()
        assert CHANGED_INFO_KEY not in session.info

    assert await generation(redis, server.id) == generation_before
    assert await cached(factory, server.id) == before == []


async def test_uncommitted_changes_are_read_from_postgres_and_never_stored(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    gen = await generation(redis, server.id)
    async with factory() as session:
        await reservations.create_reservation(
            session,
            student,
            reservations.NewReservation(server.id, 1, SATURDAY, SATURDAY + H, "Run"),
            now=NOW,
            quota_zone=QUOTA_ZONE,
        )
        # The session sees its own booking...
        mine = (await busy_intervals(session, [server.id], WINDOW))[server.id]
        assert mine == [Interval(SATURDAY, SATURDAY + H)]
        await session.rollback()

    # ...but did not publish it, so after the rollback nobody else sees it.
    assert not await redis.exists(day_key(server.id, gen, SATURDAY_UTC_DAY.start.date()))
    assert await cached(factory, server.id) == []


# --- the read/commit race ---


async def test_a_fill_that_raced_a_commit_is_stored_under_the_old_generation(
    factory: Factory, cache: AvailabilityCache, redis: Redis, student: User, server: Server
) -> None:
    """The interleaving the generation order exists for:

    reader: read generation G -> query Postgres (no booking yet)
    writer:                                     commit booking -> increment to G+1
    reader:                                                       store under G

    The stale result must land under G, which no later reader uses.
    """
    queried = asyncio.Event()
    committed = asyncio.Event()

    async def slow_load(
        session: AsyncSession, server_ids: Sequence[uuid.UUID], window: Interval
    ) -> dict[uuid.UUID, list[Hold]]:
        result = await load_holds(session, server_ids, window)
        queried.set()
        await committed.wait()
        return result

    async def reader() -> dict[uuid.UUID, list[Hold]]:
        async with factory() as session:
            return await cache.holds(session, [server.id], WINDOW, load=slow_load)

    old = await generation(redis, server.id)
    reading = asyncio.create_task(reader())
    await asyncio.wait_for(queried.wait(), 5)
    await book(factory, student, server)
    committed.set()
    stale = await asyncio.wait_for(reading, 5)

    # The reader's own answer predates the booking, as its query did.
    assert stale[server.id] == []
    # The commit moved the server on before the stale fill was stored...
    assert await generation(redis, server.id) == old + 1
    # ...so the fill sits under the old generation, where no one looks.
    assert await redis.get(day_key(server.id, old, SATURDAY_UTC_DAY.start.date())) == b"[]"
    assert await cached(factory, server.id) == [Interval(SATURDAY, SATURDAY + H)]


# --- Redis outages ---


async def test_reads_and_commits_work_while_redis_is_down(
    job_engine: AsyncEngine, factory: Factory, student: User, server: Server
) -> None:
    down = Redis.from_url(f"redis://{UNREACHABLE_HOST}/0", socket_connect_timeout=0.2)
    broken = create_session_factory(
        job_engine, availability_cache=AvailabilityCache(down, ttl_seconds=TTL)
    )
    try:
        # The commit succeeds even though the invalidation cannot reach Redis.
        reservation = await book(broken, student, server)
        assert await cached(broken, server.id) == [Interval(SATURDAY, SATURDAY + H)]
        async with broken() as session:
            await reservations.cancel_reservation(session, student, reservation.id, now=NOW)
            await session.commit()
        assert await cached(broken, server.id) == []
    finally:
        await down.aclose()


# --- marks ---


async def test_marks_from_a_savepoint_survive_until_the_commit(
    factory: Factory, redis: Redis, server: Server
) -> None:
    before = await generation(redis, server.id)
    async with factory() as session:
        async with session.begin_nested():
            mark_changed(session, server.id)
        # Releasing the savepoint fires after_commit too, but nothing is visible to
        # other connections yet, so nothing may be invalidated.
        assert server.id in session.info[CHANGED_INFO_KEY]
        assert await generation(redis, server.id) == before
        await session.commit()
    assert await generation(redis, server.id) == before + 1


async def test_a_second_booking_in_the_transaction_does_not_invalidate_early(
    factory: Factory, redis: Redis, student: User, server: Server
) -> None:
    # create_reservation allocates inside a savepoint. The second booking's savepoint
    # release must not consume the first booking's mark before the real commit.
    before = await generation(redis, server.id)
    async with factory() as session:
        for start in (SATURDAY, SATURDAY + 2 * H):
            await reservations.create_reservation(
                session,
                student,
                reservations.NewReservation(server.id, 1, start, start + H, "Run"),
                now=NOW,
                quota_zone=QUOTA_ZONE,
            )
        assert await generation(redis, server.id) == before
        await session.commit()
    assert await generation(redis, server.id) == before + 1
    assert len(await cached(factory, server.id)) == 2


# --- through the API ---


async def test_a_booking_shows_in_availability_read_just_before_it(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(db_session, "ada@example.edu")
    headers = bearer(await login(api_client, "ada@example.edu"))
    target = await add_server(db_session, await add_lab(db_session), gpu_count=2)
    start = next_saturday(datetime.now(UTC))
    params = {"from": start.isoformat(), "to": (start + H).isoformat()}
    path = f"/api/v1/servers/{target.id}/availability"

    async def free() -> list[int]:
        response = await api_client.get(path, params=params, headers=headers)
        assert response.status_code == 200, response.text
        return [run["free"] for run in response.json()["runs"]]

    assert await free() == [2]
    booked = await api_client.post(
        "/api/v1/reservations", json=booking_body(target.id, start, hours=1), headers=headers
    )
    assert booked.status_code == 201, booked.text
    assert await free() == [1]


# --- the increment after a commit fails open ---


@asynccontextmanager
async def silent_redis() -> AsyncIterator[str]:
    """A TCP server that accepts connections and never answers, like a hung Redis.
    Yields its redis:// URL."""
    held: list[asyncio.StreamWriter] = []

    async def accept(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"redis://127.0.0.1:{port}/0"
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


async def book_through_the_api(
    make_settings: Callable[..., Settings],
    client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]],
    db_session: AsyncSession,
    redis_url: str,
) -> float:
    """Book through an app whose Redis is at `redis_url`; check that the booking was
    answered with 201 and committed, and return how long the request took."""
    settings = make_settings(redis_url=redis_url)
    user = await add_user(db_session, "ada@example.edu")
    target = await add_server(db_session, await add_lab(db_session), gpu_count=2)
    token, _ = create_access_token(
        user.id, secret=settings.jwt_secret.get_secret_value(), ttl=timedelta(minutes=15)
    )
    start = next_saturday(datetime.now(UTC))

    async with client_for(settings) as client:
        began = time.monotonic()
        response = await client.post(
            "/api/v1/reservations",
            json=booking_body(target.id, start, hours=1),
            headers={"Authorization": f"Bearer {token}"},
        )
        elapsed = time.monotonic() - began

    assert response.status_code == 201, response.text
    stored = await db_session.get(Reservation, uuid.UUID(response.json()["id"]))
    assert stored is not None and stored.server_id == target.id
    return elapsed


async def test_a_booking_is_created_when_redis_refuses_connections(
    make_settings: Callable[..., Settings],
    client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]],
    db_session: AsyncSession,
) -> None:
    await book_through_the_api(
        make_settings, client_for, db_session, f"redis://{UNREACHABLE_HOST}/0"
    )


async def test_a_booking_is_created_when_redis_hangs(
    make_settings: Callable[..., Settings],
    client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with silent_redis() as url:
        elapsed = await book_through_the_api(make_settings, client_for, db_session, url)

    # Bounded by the invalidation timeout (0.15 s), not by Redis: without the limit the
    # request would wait forever. A fixed bound, so raising the timeout fails the test.
    assert elapsed < 1.0
    assert "Could not invalidate cached availability" in caplog.text


class ExplodingRedis:
    """Fails in a way no Redis client error describes."""

    def pipeline(self, **_: object) -> NoReturn:
        raise RuntimeError("unexpected")


async def test_an_unexpected_error_after_the_commit_is_logged_not_raised(
    job_engine: AsyncEngine,
    student: User,
    server: Server,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = AvailabilityCache(ExplodingRedis(), ttl_seconds=TTL)  # type: ignore[arg-type]
    exploding = create_session_factory(job_engine, availability_cache=cache)

    reservation = await book(exploding, student, server)

    async with exploding() as session:
        assert await session.get(Reservation, reservation.id) is not None
    assert "RuntimeError: unexpected" in caplog.text
