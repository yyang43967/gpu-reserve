"""The load-test data and checks: the same data on every run, a correct race check,
and hit counting that is off unless asked for."""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app import loadtest
from app.core.config import Environment, Settings
from app.core.exceptions import AllocationContention, GpuUnavailable
from app.core.security import decode_access_token
from app.db.session import create_session_factory
from app.models import GpuAllocation, Reservation, User
from app.services import reservations
from app.services.availability_cache import (
    STATS_KEY,
    AvailabilityCache,
    busy_intervals,
    generation_key,
    load_holds,
)
from app.services.booking_rules import Interval

pytestmark = pytest.mark.integration

NOW = datetime(2030, 1, 7, 18, 7, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def factory(job_engine: AsyncEngine) -> Factory:
    return create_session_factory(job_engine)


@pytest.fixture
async def fixture(factory: Factory, make_settings: Callable[..., Settings]) -> dict[str, Any]:
    async with factory() as session:
        data = await loadtest.seed(session, make_settings(), now=datetime.now(UTC))
        await session.commit()
    return data


# --- the plan ---


def test_the_same_seed_gives_the_same_rows() -> None:
    assert loadtest.plan(NOW) == loadtest.plan(NOW)


def test_a_later_run_gets_the_same_rows_shifted_in_time() -> None:
    first, later = loadtest.plan(NOW), loadtest.plan(NOW + timedelta(days=1))
    assert [r["id"] for r in first.reservations] == [r["id"] for r in later.reservations]
    assert all(
        b["starts_at"] - a["starts_at"] == timedelta(days=1)
        for a, b in zip(first.reservations, later.reservations, strict=True)
    )


def test_no_gpu_is_planned_twice_at_once() -> None:
    plan = loadtest.plan(NOW)
    by_gpu: dict[Any, list[tuple[datetime, datetime]]] = defaultdict(list)
    for allocation in plan.allocations:
        if allocation["active"]:
            by_gpu[allocation["gpu_id"]].append((allocation["starts_at"], allocation["ends_at"]))
    for ranges in by_gpu.values():
        ranges.sort()
        assert all(a_end <= b_start for (_, a_end), (b_start, _) in pairwise(ranges))


def test_the_race_slot_is_off_peak_in_every_lab() -> None:
    start, end = loadtest.race_slot(NOW)
    assert start > NOW and end - start == timedelta(hours=loadtest.RACE_HOURS)
    for spec in loadtest.LABS:
        local = start.astimezone(ZoneInfo(spec.timezone))
        assert local.weekday() >= 5 or not 9 <= local.hour < 18


def test_token_lifetime_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(loadtest.TOKEN_TTL_ENV, raising=False)
    assert loadtest.token_ttl() == timedelta(minutes=120)
    monkeypatch.setenv(loadtest.TOKEN_TTL_ENV, "240")
    assert loadtest.token_ttl() == timedelta(minutes=240)
    for bad in ("soon", "0"):
        monkeypatch.setenv(loadtest.TOKEN_TTL_ENV, bad)
        with pytest.raises(loadtest.LoadTestError):
            loadtest.token_ttl()


# --- seeding ---


async def test_seeding_inserts_the_plan_and_long_lived_tokens(
    factory: Factory, fixture: dict[str, Any], make_settings: Callable[..., Settings]
) -> None:
    async with factory() as session:
        stored = await session.scalar(select(func.count()).select_from(GpuAllocation))
        users = await session.scalar(select(func.count()).select_from(User))
    assert stored == fixture["counts"]["allocations"] > 10_000
    assert users == fixture["counts"]["users"]
    assert len(fixture["students"]) == loadtest.STUDENTS
    assert len(fixture["racers"]) == loadtest.RACERS

    secret = make_settings().jwt_secret.get_secret_value()
    student_id = decode_access_token(fixture["students"][0], secret=secret)
    async with factory() as session:
        student = await session.get(User, student_id)
        # The scripts act only as accounts that hold no bookings.
        held = await session.scalar(
            select(func.count()).select_from(Reservation).where(Reservation.user_id == student_id)
        )
    assert student is not None and student.email.startswith("student")
    assert held == 0
    expires = datetime.fromisoformat(fixture["token_expires_at"])
    assert expires - datetime.fromisoformat(fixture["generated_at"]) == timedelta(minutes=120)


async def test_seeding_again_replaces_everything(
    factory: Factory, fixture: dict[str, Any], make_settings: Callable[..., Settings]
) -> None:
    async with factory() as session:
        again = await loadtest.seed(session, make_settings(), now=datetime.now(UTC))
        await session.commit()
        stored = await session.scalar(select(func.count()).select_from(Reservation))
    assert again["counts"] == fixture["counts"]
    assert stored == fixture["counts"]["reservations"]


async def test_clearing_the_cache_leaves_other_keys(redis: Redis) -> None:
    await redis.set(generation_key(loadtest.uuid.uuid4()), 3)
    await redis.hset(STATS_KEY, "hits", 1)
    await redis.set("rate:login:ip:192.0.2.1", 1)
    assert await loadtest.clear_cache(redis) == 2
    assert await redis.exists("rate:login:ip:192.0.2.1")


# --- the race check ---


async def test_verify_after_a_real_race(
    factory: Factory, fixture: dict[str, Any], make_settings: Callable[..., Settings]
) -> None:
    async with factory() as session:
        before = await loadtest.verify(session)
    assert before["ok"] is False and before["race_winners"] == 0

    race = fixture["race"]
    start = datetime.fromisoformat(race["starts_at"])
    end = datetime.fromisoformat(race["ends_at"])
    settings = make_settings()
    async with factory() as session:
        racers = list(
            await session.scalars(
                select(User).where(User.email.startswith("racer")).order_by(User.email).limit(12)
            )
        )

    async def attempt(user: User) -> bool:
        async with factory() as session:
            try:
                await reservations.create_reservation(
                    session,
                    user,
                    reservations.NewReservation(
                        loadtest.uuid.UUID(race["server_id"]),
                        race["gpu_count"],
                        start,
                        end,
                        "Race",
                    ),
                    now=datetime.now(UTC),
                    quota_zone=settings.quota_zone,
                )
            except (GpuUnavailable, AllocationContention):
                return False
            await session.commit()
            return True

    won = await asyncio.gather(*(attempt(user) for user in racers))

    async with factory() as session:
        after = await loadtest.verify(session)
    assert sum(won) == race["expected_successes"] == after["race_winners"]
    assert after["overlapping_active_allocations"] == 0
    assert after["race_bookings_per_gpu"] == dict.fromkeys(range(loadtest.RACE_GPUS), 1)
    assert after["ok"] is True


# --- hit counting ---


async def test_hits_are_counted_only_when_asked(
    job_engine: AsyncEngine, fixture: dict[str, Any], redis: Redis
) -> None:
    server_id = loadtest.uuid.UUID(fixture["servers"][0]["id"])
    # From midnight UTC tomorrow for 2 days and an hour: three UTC days.
    tomorrow = datetime.fromisoformat(fixture["generated_at"]).date() + timedelta(days=1)
    start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)
    window = Interval(start, start + timedelta(days=2, hours=1))

    quiet = create_session_factory(
        job_engine, availability_cache=AvailabilityCache(redis, ttl_seconds=60)
    )
    async with quiet() as session:
        await busy_intervals(session, [server_id], window)
    assert not await redis.exists(STATS_KEY)

    await redis.flushdb()
    counting = AvailabilityCache(redis, ttl_seconds=60, stats=True)
    async with create_session_factory(job_engine)() as session:
        cold = await counting.holds(session, [server_id], window)
        warm = await counting.holds(session, [server_id], window)
        direct = await load_holds(session, [server_id], window)
    assert cold == warm
    assert {h.id for h in warm[server_id]} == {h.id for h in direct[server_id]}

    # Three UTC days, missed once and then hit.
    assert await loadtest.cache_stats(redis, reset_after=True) == {
        "hits": 3,
        "misses": 3,
        "hit_rate": 0.5,
    }
    assert not await redis.exists(STATS_KEY)


async def test_the_commands_refuse_outside_the_test_environment(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(loadtest.LoadTestError):
        await loadtest._run("seed", make_settings(environment=Environment.LOCAL), reset_stats=False)
