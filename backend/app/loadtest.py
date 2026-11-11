"""Data and checks for the load tests in loadtest/.

    python -m app.loadtest seed          # reset, create the data, print the fixture JSON
    python -m app.loadtest verify        # after a booking race: correctness checks as JSON
    python -m app.loadtest cache-stats [--reset]   # cache hit and miss counts as JSON

Only with ENVIRONMENT=test, like `python -m app.cli reset-database`: `seed` empties
every table first. The load-test Compose stack sets it.

The data is the same on every run: ids, names, and the shape of the schedule come from
a fixed random seed, and times are offsets from the current 30-minute boundary. Bookings
are inserted directly rather than through create_reservation, because thousands of
historical bookings would otherwise need a clock per booking; the generator still never
puts two active allocations on one GPU at once, and the exclusion constraint checks
that on insert. Quota, peak-hours, and active-reservation rules are not applied to the
generated bookings, so they belong to a separate set of "holder" accounts that the load
scripts never act as. The scripts use accounts with no bookings, whose searches and
bookings follow every rule.

Access tokens are minted here so the scripts never call the login endpoint (which is
rate limited per address). They last LOADTEST_TOKEN_TTL_MINUTES (default 120), long
enough for any run; the API's own token lifetime is unchanged.
"""

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from redis.asyncio import Redis
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Environment, Settings
from app.core.security import create_access_token
from app.db.session import create_engine, create_session_factory
from app.models import (
    CreatedVia,
    Gpu,
    GpuAllocation,
    GpuModel,
    Lab,
    Reservation,
    ReservationStatus,
    Server,
    User,
    UserRole,
)
from app.models.server import GPU_MEMORY_GB
from app.seed import LABS, reset
from app.services.availability_cache import KEY_PREFIX, STATS_KEY
from app.services.booking_rules import SLOT, floor_to_grid

RANDOM_SEED: Final = 20301
TOKEN_TTL_ENV: Final = "LOADTEST_TOKEN_TTL_MINUTES"  # noqa: S105 - a variable name
DEFAULT_TOKEN_TTL_MINUTES: Final = 120

SERVERS_PER_LAB: Final = 10
GPU_COUNTS: Final = (2, 4, 8)
MODELS: Final = (
    GpuModel.RTX_4090,
    GpuModel.RTX_6000_ADA,
    GpuModel.L40S,
    GpuModel.A100_40GB,
    GpuModel.A100_80GB,
    GpuModel.H100_80GB,
)
HOLDERS: Final = 50
STUDENTS: Final = 200
RACERS: Final = 100
HISTORY: Final = timedelta(days=60)
FUTURE: Final = timedelta(days=14)
# Bookings take 1 to 8 hours with gaps of 0 to 6 hours: GPUs are busy a bit over
# half the time, so availability has real structure and slot search has to look.
DURATION_SLOTS: Final = (2, 16)
GAP_SLOTS: Final = (0, 12)
BOOKING_SIZES: Final = (1, 1, 2, 2, 4, 8)
INSERT_CHUNK: Final = 5000

RACE_HOSTNAME: Final = "race-a100-01"
RACE_GPUS: Final = 8
RACE_GPUS_PER_BOOKING: Final = 2
RACE_DAYS_AHEAD: Final = 3
RACE_HOURS: Final = 1

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1


class LoadTestError(Exception):
    pass


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


@dataclass
class _Plan:
    """Rows to insert, built in memory first."""

    labs: list[dict[str, Any]]
    servers: list[dict[str, Any]]
    gpus: list[dict[str, Any]]
    users: list[dict[str, Any]]
    reservations: list[dict[str, Any]]
    allocations: list[dict[str, Any]]


def _user(rng: random.Random, email: str, role: UserRole = UserRole.STUDENT) -> dict[str, Any]:
    return {
        "id": _uuid(rng),
        "email": email,
        # Nobody signs in as these accounts; tokens are minted.
        "password_hash": "!",
        "full_name": email.split("@")[0],
        "role": role,
    }


def plan(now: datetime) -> _Plan:
    """The rows for a run starting at `now`. Same seed, same rows, shifted in time."""
    rng = random.Random(RANDOM_SEED)  # noqa: S311 - reproducible test data, not secrets
    anchor = floor_to_grid(now)
    p = _Plan([], [], [], [], [], [])

    for spec in LABS:
        p.labs.append(
            {
                "id": _uuid(rng),
                "name": spec.name,
                "location": spec.location,
                "timezone": spec.timezone,
                "description": spec.description,
            }
        )

    for lab_index, lab in enumerate(p.labs):
        for number in range(SERVERS_PER_LAB):
            model = MODELS[(lab_index + number) % len(MODELS)]
            p.servers.append(
                {
                    "id": _uuid(rng),
                    "lab_id": lab["id"],
                    "hostname": f"{LABS[lab_index].key}-{model.value.lower()}-{number:02d}",
                    "gpu_model": model,
                    "gpu_memory_gb": GPU_MEMORY_GB[model],
                    "gpu_count": rng.choice(GPU_COUNTS),
                    "cpu_cores": 64,
                    "ram_gb": 512,
                    "requires_approval": model is GpuModel.H100_80GB,
                }
            )
    race = {
        "id": _uuid(rng),
        "lab_id": p.labs[0]["id"],
        "hostname": RACE_HOSTNAME,
        "gpu_model": GpuModel.A100_80GB,
        "gpu_memory_gb": GPU_MEMORY_GB[GpuModel.A100_80GB],
        "gpu_count": RACE_GPUS,
        "cpu_cores": 128,
        "ram_gb": 1024,
        "requires_approval": False,
    }
    p.servers.append(race)

    gpus_by_server: dict[uuid.UUID, list[uuid.UUID]] = {}
    for server in p.servers:
        ids = [_uuid(rng) for _ in range(server["gpu_count"])]
        gpus_by_server[server["id"]] = ids
        p.gpus.extend(
            {"id": gpu_id, "server_id": server["id"], "index": index}
            for index, gpu_id in enumerate(ids)
        )

    p.users.append(_user(rng, "admin@loadtest.example.edu", UserRole.ADMIN))
    holders = [_user(rng, f"holder{n:03d}@loadtest.example.edu") for n in range(HOLDERS)]
    p.users.extend(holders)
    p.users.extend(_user(rng, f"student{n:03d}@loadtest.example.edu") for n in range(STUDENTS))
    p.users.extend(_user(rng, f"racer{n:03d}@loadtest.example.edu") for n in range(RACERS))

    begin, end = anchor - HISTORY, anchor + FUTURE
    for server in p.servers:
        if server is race:
            continue
        gpu_ids = gpus_by_server[server["id"]]
        free_at = dict.fromkeys(gpu_ids, begin)
        while True:
            size = min(rng.choice(BOOKING_SIZES), len(gpu_ids))
            # The GPUs that free up first, lowest number on ties, like the allocator.
            chosen = sorted(gpu_ids, key=lambda g: (free_at[g], gpu_ids.index(g)))[:size]
            start = max(free_at[g] for g in chosen) + rng.randint(*GAP_SLOTS) * SLOT
            finish = start + rng.randint(*DURATION_SLOTS) * SLOT
            if start >= end:
                break
            for gpu_id in chosen:
                free_at[gpu_id] = finish
            if finish <= anchor:
                status = ReservationStatus.COMPLETED
            elif server["requires_approval"] and start > anchor and rng.random() < 0.5:
                status = ReservationStatus.PENDING
            else:
                status = ReservationStatus.APPROVED
            reservation_id = _uuid(rng)
            p.reservations.append(
                {
                    "id": reservation_id,
                    "user_id": rng.choice(holders)["id"],
                    "server_id": server["id"],
                    "gpu_count": size,
                    "starts_at": start,
                    "ends_at": finish,
                    "status": status,
                    "purpose": "Load-test booking",
                    "created_via": CreatedVia.WEB,
                }
            )
            active = status is not ReservationStatus.COMPLETED
            p.allocations.extend(
                {
                    "id": _uuid(rng),
                    "reservation_id": reservation_id,
                    "gpu_id": gpu_id,
                    "starts_at": start,
                    "ends_at": finish,
                    "active": active,
                }
                for gpu_id in chosen
            )
    return p


def race_slot(now: datetime) -> tuple[datetime, datetime]:
    """The slot every racer asks for: a few days ahead, at 03:00 UTC (night in every
    lab's zone, so outside peak hours)."""
    day = (floor_to_grid(now) + timedelta(days=RACE_DAYS_AHEAD)).date()
    start = datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)
    return start, start + timedelta(hours=RACE_HOURS)


def token_ttl() -> timedelta:
    raw = os.environ.get(TOKEN_TTL_ENV, str(DEFAULT_TOKEN_TTL_MINUTES))
    try:
        minutes = int(raw)
    except ValueError as exc:
        raise LoadTestError(f"{TOKEN_TTL_ENV} must be a whole number of minutes.") from exc
    if minutes <= 0:
        raise LoadTestError(f"{TOKEN_TTL_ENV} must be positive.")
    return timedelta(minutes=minutes)


async def _insert(session: AsyncSession, model: type[Any], rows: Sequence[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), INSERT_CHUNK):
        await session.execute(insert(model), list(rows[offset : offset + INSERT_CHUNK]))


async def seed(session: AsyncSession, settings: Settings, *, now: datetime) -> dict[str, Any]:
    """Reset the database, insert the plan, and return the fixture the scripts read.
    The caller commits."""
    p = plan(now)
    await reset(session)
    await _insert(session, Lab, p.labs)
    await _insert(session, Server, p.servers)
    await _insert(session, Gpu, p.gpus)
    await _insert(session, User, p.users)
    await _insert(session, Reservation, p.reservations)
    await _insert(session, GpuAllocation, p.allocations)
    # Fresh statistics, so the planner sees the same table sizes on every run.
    await session.execute(text("ANALYZE"))

    secret = settings.jwt_secret.get_secret_value()
    ttl = token_ttl()

    def tokens(prefix: str) -> list[str]:
        return [
            create_access_token(u["id"], secret=secret, ttl=ttl, now=now)[0]
            for u in p.users
            if u["email"].startswith(prefix)
        ]

    race_start, race_end = race_slot(now)
    race = next(s for s in p.servers if s["hostname"] == RACE_HOSTNAME)
    return {
        "seed": RANDOM_SEED,
        "generated_at": now.isoformat(),
        "token_expires_at": (now + ttl).isoformat(),
        "counts": {
            "servers": len(p.servers),
            "users": len(p.users),
            "reservations": len(p.reservations),
            "allocations": len(p.allocations),
            "active_allocations": sum(1 for a in p.allocations if a["active"]),
        },
        "labs": [str(lab["id"]) for lab in p.labs],
        "servers": [
            {
                "id": str(s["id"]),
                "hostname": s["hostname"],
                "gpu_model": s["gpu_model"].value,
                "gpu_count": s["gpu_count"],
                "gpu_memory_gb": s["gpu_memory_gb"],
                "lab_id": str(s["lab_id"]),
            }
            for s in p.servers
        ],
        "students": tokens("student"),
        "racers": tokens("racer"),
        "race": {
            "server_id": str(race["id"]),
            "gpu_count": RACE_GPUS_PER_BOOKING,
            "starts_at": race_start.isoformat(),
            "ends_at": race_end.isoformat(),
            "expected_successes": RACE_GPUS // RACE_GPUS_PER_BOOKING,
        },
    }


async def clear_cache(redis: Redis) -> int:
    """Delete every availability entry, generation, and the hit counters."""
    deleted = 0
    async for key in redis.scan_iter(match=f"{KEY_PREFIX}:*", count=1000):
        deleted += await redis.delete(key)
    return deleted


_OVERLAPPING_ACTIVE = text("""
SELECT count(*)
FROM gpu_allocations a
JOIN gpu_allocations b
  ON a.gpu_id = b.gpu_id AND a.id < b.id
 AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')
WHERE a.active AND b.active
""")


async def verify(session: AsyncSession) -> dict[str, Any]:
    """Correctness after a booking race: no GPU holds two overlapping active
    allocations anywhere, and on the race server each GPU was booked at most once and
    exactly as many bookings won as the GPUs allow."""
    race = await session.scalar(select(Server).where(Server.hostname == RACE_HOSTNAME))
    if race is None:
        raise LoadTestError("No race server; run `seed` first.")
    overlapping = int(await session.scalar(_OVERLAPPING_ACTIVE) or 0)
    winners = int(
        await session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.server_id == race.id,
                Reservation.status.in_([ReservationStatus.APPROVED, ReservationStatus.PENDING]),
            )
        )
        or 0
    )
    per_gpu = (
        await session.execute(
            select(Gpu.index, func.count(GpuAllocation.id))
            .join(GpuAllocation, GpuAllocation.gpu_id == Gpu.id)
            .where(Gpu.server_id == race.id, GpuAllocation.active.is_(True))
            .group_by(Gpu.index)
            .order_by(Gpu.index)
        )
    ).all()
    bookings_per_gpu = {int(index): int(count) for index, count in per_gpu}
    expected = race.gpu_count // RACE_GPUS_PER_BOOKING
    return {
        "overlapping_active_allocations": overlapping,
        "race_winners": winners,
        "race_expected_winners": expected,
        "race_bookings_per_gpu": bookings_per_gpu,
        "ok": overlapping == 0
        and winners == expected
        and len(bookings_per_gpu) == race.gpu_count
        and all(count == 1 for count in bookings_per_gpu.values()),
    }


async def cache_stats(redis: Redis, *, reset_after: bool) -> dict[str, Any]:
    # Through a pipeline: redis-py types the plain call as maybe-synchronous.
    async with redis.pipeline(transaction=False) as pipe:
        pipe.hgetall(STATS_KEY)
        [raw] = await pipe.execute()
    counts = {key.decode(): int(value) for key, value in raw.items()}
    hits, misses = counts.get("hits", 0), counts.get("misses", 0)
    if reset_after:
        await redis.delete(STATS_KEY)
    total = hits + misses
    return {"hits": hits, "misses": misses, "hit_rate": hits / total if total else None}


async def _run(command: str, settings: Settings, *, reset_stats: bool) -> dict[str, Any]:
    if settings.environment is not Environment.TEST:
        raise LoadTestError("app.loadtest only runs with ENVIRONMENT=test.")
    engine = create_engine(settings)
    redis = Redis.from_url(str(settings.redis_url))
    try:
        if command == "cache-stats":
            return await cache_stats(redis, reset_after=reset_stats)
        async with create_session_factory(engine)() as session:
            if command == "verify":
                return await verify(session)
            fixture = await seed(session, settings, now=datetime.now(UTC))
            await session.commit()
        fixture["cache_keys_deleted"] = await clear_cache(redis)
        return fixture
    finally:
        await redis.aclose()
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.loadtest", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed", help="Reset the database and create the load-test data.")
    commands.add_parser("verify", help="Check booking correctness after a race.")
    stats = commands.add_parser("cache-stats", help="Print availability cache hit counts.")
    stats.add_argument("--reset", action="store_true", help="Zero the counts after reading.")
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(
            _run(args.command, Settings(), reset_stats=getattr(args, "reset", False))
        )
    except LoadTestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
