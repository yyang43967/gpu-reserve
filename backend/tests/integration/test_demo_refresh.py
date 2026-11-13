"""The nightly replay of the demo plan: it keeps the demo current at any moment, never
touches data the seed did not create, and lets real bookings win."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cli import CommandError, refresh_demo
from app.core.config import Settings
from app.core.security import UNUSABLE_PASSWORD_HASH
from app.models import (
    AuditLog,
    Notification,
    NotificationKind,
    Reservation,
    ReservationStatus,
    Server,
    User,
    UserRole,
)
from app.seed import seed
from app.services import demo_plan, locks, passwords, reservations
from app.services.demo_plan import DEMO_ADMIN_EMAIL, PLAN, Booking
from app.services.demo_refresh import NothingSeeded, refresh_seeded_reservations
from app.services.reservations import NewReservation
from tests.integration.reservation_helpers import QUOTA_ZONE, add_user
from tests.integration.test_seed import SWEEP

pytestmark = pytest.mark.integration

PASSWORD: Final = "seed-password-for-tests"
SEEDED_AT: Final = datetime(2030, 1, 6, 20, 0, tzinfo=UTC)
# Two weeks later, when everything seeded is in the past: the case the refresh is for.
LATER: Final = SEEDED_AT + timedelta(days=15)
LA: Final = ZoneInfo("America/Los_Angeles")


async def _fast_hash(password: str) -> str:
    return f"plain:{password}"


@pytest.fixture(autouse=True)
def cheap_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(passwords, "hash_password", _fast_hash)


async def _seed(db: AsyncSession, now: datetime = SEEDED_AT) -> None:
    await seed(
        db,
        now=now,
        admin_email="admin@example.edu",
        admin_password=PASSWORD,
        student_password=None,
        quota_zone=QUOTA_ZONE,
    )
    await db.commit()


async def _refresh(db: AsyncSession, now: datetime):
    result = await refresh_seeded_reservations(db, now=now, quota_zone=QUOTA_ZONE)
    await db.commit()
    return result


async def _overlaps(db: AsyncSession) -> int:
    count = await db.scalar(
        text(
            "SELECT count(*) FROM gpu_allocations a JOIN gpu_allocations b "
            "ON a.gpu_id = b.gpu_id AND a.id < b.id AND a.active AND b.active "
            "AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')"
        )
    )
    return count or 0


async def test_the_refresh_holds_whenever_it_runs(db_session: AsyncSession) -> None:
    """Refreshed again and again, at every weekday, time of day, and around both
    daylight saving changes: every plan entry is booked each time, with no rule broken."""
    await _seed(db_session)

    for now in SWEEP:
        result = await _refresh(db_session, now)

        assert result.skipped == (), now
        assert result.created == len(PLAN), now
        assert set(result.by_status) == {status.value for status in ReservationStatus}, now
        assert await _overlaps(db_session) == 0, now
        total = await db_session.scalar(select(func.count()).select_from(Reservation))
        assert total == len(PLAN), now
        upcoming = await db_session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                Reservation.status.in_([ReservationStatus.PENDING, ReservationStatus.APPROVED]),
                Reservation.starts_at > now,
            )
        )
        assert upcoming and upcoming >= 10, now
        stale = await db_session.scalar(
            select(func.count())
            .select_from(Reservation)
            .where(
                ((Reservation.status == ReservationStatus.PENDING) & (Reservation.starts_at <= now))
                | (
                    (Reservation.status == ReservationStatus.APPROVED)
                    & (Reservation.ends_at <= now)
                )
            )
        )
        assert stale == 0, now
        # Replaying history emails nobody.
        assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0


async def test_it_moves_the_demo_forward_and_runs_twice_to_the_same_result(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    first_ids = set(await db_session.scalars(select(Reservation.id)))

    first = await _refresh(db_session, LATER)
    second = await _refresh(db_session, LATER)

    assert (first.deleted, first.created) == (len(PLAN), len(PLAN))
    assert (second.deleted, second.created) == (len(PLAN), len(PLAN))
    assert first.by_status == second.by_status
    rows = list(await db_session.scalars(select(Reservation)))
    assert not first_ids & {r.id for r in rows}
    assert all(r.seeded for r in rows)
    assert max(r.starts_at for r in rows) > LATER + timedelta(days=10)
    audits = await db_session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "demo.refresh")
    )
    assert audits == 2


async def test_real_data_is_never_touched_and_real_bookings_win(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    real_admin = await db_session.scalar(select(User).where(User.email == "admin@example.edu"))
    assert real_admin is not None
    admin_before = (real_admin.updated_at, real_admin.password_hash, real_admin.seeded)
    registered = await add_user(db_session, "someone@example.org")
    # A real booking in the slot of the plan's 8-GPU run on vision-a100-01 (tomorrow
    # 19:00 Los Angeles), made before the refresh.
    server = await db_session.scalar(select(Server).where(Server.hostname == "vision-a100-01"))
    assert server is not None
    tomorrow = (LATER.astimezone(LA) + timedelta(days=1)).date()
    start = datetime.combine(tomorrow, time(19), tzinfo=LA).astimezone(UTC)
    real = await reservations.create_reservation(
        db_session,
        registered,
        NewReservation(server.id, 1, start, start + timedelta(hours=2), "A real job"),
        now=LATER - timedelta(hours=1),
        quota_zone=QUOTA_ZONE,
    )
    db_session.add(
        Notification(
            reservation_id=real.id,
            recipient_user_id=registered.id,
            kind=NotificationKind.REMINDER,
            next_attempt_at=LATER,
        )
    )
    await db_session.commit()
    real_id, admin_id = real.id, real_admin.id
    real_before = (real.status, real.updated_at, real.starts_at, real.ends_at)

    result = await _refresh(db_session, LATER)

    assert [entry.purpose for entry in result.skipped] == ["Multi-node data-parallel training test"]
    assert result.created == len(PLAN) - 1
    db_session.expire_all()
    kept = await db_session.get(Reservation, real_id)
    assert kept is not None
    assert (kept.status, kept.updated_at, kept.starts_at, kept.ends_at) == real_before
    assert not kept.seeded
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.reservation_id == real_id)
        )
        == 1
    )
    admin = await db_session.get(User, admin_id)
    assert admin is not None
    assert (admin.updated_at, admin.password_hash, admin.seeded) == admin_before
    # The real admin neither owns nor decided anything the refresh made.
    involved = await db_session.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.seeded,
            (Reservation.user_id == admin_id) | (Reservation.decided_by == admin_id),
        )
    )
    assert involved == 0
    assert await _overlaps(db_session) == 0


async def test_it_creates_the_demo_admin_for_a_database_seeded_before_it_existed(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    # Production was seeded before the demo admin existed: its decisions were made by
    # the deployment's admin. Stand-in: the demo admin becomes an unseeded admin.
    await db_session.execute(
        update(User)
        .where(User.email == DEMO_ADMIN_EMAIL)
        .values(email="former.admin@example.edu", seeded=False)
    )
    await db_session.commit()

    await _refresh(db_session, LATER)

    demo_admin = await db_session.scalar(select(User).where(User.email == DEMO_ADMIN_EMAIL))
    assert demo_admin is not None
    assert (demo_admin.role, demo_admin.seeded) == (UserRole.ADMIN, True)
    assert demo_admin.password_hash == UNUSABLE_PASSWORD_HASH
    deciders = set(
        await db_session.scalars(
            select(Reservation.decided_by).where(Reservation.decided_by.is_not(None)).distinct()
        )
    )
    assert deciders == {demo_admin.id}


async def test_a_database_that_was_never_seeded_is_refused(db_session: AsyncSession) -> None:
    await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)

    with pytest.raises(NothingSeeded):
        await refresh_seeded_reservations(db_session, now=LATER, quota_zone=QUOTA_ZONE)
    await db_session.rollback()

    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


async def test_the_command_reports_a_dry_run_and_changes_nothing(
    db_session: AsyncSession, make_settings: Callable[..., Settings], redis_url: str
) -> None:
    settings: Settings = make_settings()
    await _seed(db_session)
    before = set(await db_session.scalars(select(Reservation.id)))
    server = await db_session.scalar(select(Server).where(Server.hostname == "vision-a100-01"))
    assert server is not None
    redis = Redis.from_url(redis_url)
    generation_key = f"availability:v1:{server.id}:generation"
    generation = int(await redis.get(generation_key) or 0)

    message = await refresh_demo(settings, dry_run=True, now=LATER)

    assert message.startswith(f"Deleted {len(PLAN)} seeded reservations and booked {len(PLAN)} (")
    assert message.endswith("Dry run: rolled back, nothing was changed.")
    db_session.expire_all()
    assert set(await db_session.scalars(select(Reservation.id))) == before
    assert int(await redis.get(generation_key) or 0) == generation

    message = await refresh_demo(settings, dry_run=False, now=LATER)

    assert "Dry run" not in message
    db_session.expire_all()
    assert not before & set(await db_session.scalars(select(Reservation.id)))
    # The real run invalidated cached availability for the servers it changed.
    assert int(await redis.get(generation_key) or 0) > generation
    await redis.aclose()


async def test_the_command_reports_an_unseeded_database(
    db_session: AsyncSession, make_settings: Callable[..., Settings]
) -> None:
    with pytest.raises(CommandError, match="No seeded students"):
        await refresh_demo(make_settings(), dry_run=True, now=LATER)


# --- transient failures and lock order ---

FAILING_ENTRY: Final = "Evaluating quantized 70B models"


def _fail_one_entry(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Make one plan entry fail with a real Postgres error carrying `code`, after its
    booking was written, as a deadlock found late in the entry would."""
    original = demo_plan.book

    async def book(session: AsyncSession, booking: Booking, **kwargs: Any) -> Reservation:
        reservation = await original(session, booking, **kwargs)
        if booking.purpose == FAILING_ENTRY:
            await session.execute(
                text(f"DO $$ BEGIN RAISE EXCEPTION 'injected' USING ERRCODE = '{code}'; END $$")
            )
        return reservation

    monkeypatch.setattr(demo_plan, "book", book)


@pytest.mark.parametrize(
    ("code", "reason"), [("40P01", "deadlock detected"), ("40001", "serialization failure")]
)
async def test_a_transient_failure_skips_only_that_entry(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, code: str, reason: str
) -> None:
    await _seed(db_session)
    _fail_one_entry(monkeypatch, code)

    result = await _refresh(db_session, LATER)

    assert [(s.purpose, s.reason) for s in result.skipped] == [
        (FAILING_ENTRY, f"{reason}; retried next run")
    ]
    assert result.created == len(PLAN) - 1
    purposes = list(await db_session.scalars(select(Reservation.purpose)))
    assert FAILING_ENTRY not in purposes
    assert len(purposes) == len(PLAN) - 1
    assert await _overlaps(db_session) == 0


async def test_any_other_database_error_fails_the_whole_refresh(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    before = set(await db_session.scalars(select(Reservation.id)))
    _fail_one_entry(monkeypatch, "23503")

    with pytest.raises(DBAPIError):
        await refresh_seeded_reservations(db_session, now=LATER, quota_zone=QUOTA_ZONE)
    await db_session.rollback()

    assert set(await db_session.scalars(select(Reservation.id))) == before


async def _waiting_for_advisory_lock(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        waiting = await conn.scalar(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
        )
    return bool(waiting)


async def test_user_locks_come_before_any_reservation_row(
    db_session: AsyncSession, engine: AsyncEngine
) -> None:
    """Deactivating a seeded student takes the user lock, then that user's rows. The
    refresh must take the user lock first too, or the two could deadlock."""
    await _seed(db_session)
    ada = await db_session.scalar(select(User).where(User.email == "ada.lovelace@example.edu"))
    assert ada is not None
    sessions = async_sessionmaker(engine)

    async with sessions() as holder, sessions() as refreshing:
        # Autobegins a transaction that holds Ada's user lock until the commit below.
        await locks.lock_user_bookings(holder, ada.id)
        task = asyncio.create_task(_refresh(refreshing, LATER))
        for _ in range(200):
            if await _waiting_for_advisory_lock(engine):
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the refresh never waited for the user lock")
        # Blocked on a user lock, the refresh holds no reservation row yet.
        async with sessions() as probe, probe.begin():
            locked = await probe.execute(
                select(Reservation.id).where(Reservation.seeded).with_for_update(nowait=True)
            )
            assert len(locked.all()) == len(PLAN)
        assert not task.done()

        await holder.commit()
        result = await asyncio.wait_for(task, timeout=30)

    assert result.created == len(PLAN)
