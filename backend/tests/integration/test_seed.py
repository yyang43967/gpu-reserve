"""The demo data seed: it must hold at any moment it is run."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import CommandError
from app.core.config import Environment, Settings
from app.models import (
    Lab,
    Notification,
    Reservation,
    ReservationStatus,
    Server,
    ServerStatus,
    User,
    UserRole,
)
from app.seed import DatabaseNotEmpty, run, seed
from app.services import passwords
from tests.integration.reservation_helpers import QUOTA_ZONE

pytestmark = pytest.mark.integration

PASSWORD: Final = "seed-password-for-tests"
NOW: Final = datetime(2030, 1, 9, 17, 40, tzinfo=UTC)  # a Wednesday morning in Los Angeles


async def _fast_hash(password: str) -> str:
    return f"plain:{password}"


@pytest.fixture
def cheap_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """argon2 is deliberately slow; the sweep below seeds many times."""
    monkeypatch.setattr(passwords, "hash_password", _fast_hash)


async def _seed(db: AsyncSession, now: datetime = NOW, student_password: str | None = PASSWORD):
    summary = await seed(
        db,
        now=now,
        admin_email="admin@example.edu",
        admin_password=PASSWORD,
        student_password=student_password,
        quota_zone=QUOTA_ZONE,
    )
    await db.commit()
    return summary


async def _overlapping_active_allocations(db: AsyncSession) -> int:
    count = await db.scalar(
        text(
            "SELECT count(*) FROM gpu_allocations a JOIN gpu_allocations b "
            "ON a.gpu_id = b.gpu_id AND a.id < b.id AND a.active AND b.active "
            "AND tstzrange(a.starts_at, a.ends_at, '[)') && tstzrange(b.starts_at, b.ends_at, '[)')"
        )
    )
    return count or 0


async def test_seed_creates_the_described_data(db_session: AsyncSession) -> None:
    summary = await _seed(db_session)

    assert (summary.labs, summary.servers, summary.students) == (3, 10, 8)
    assert set(summary.by_status) == {status.value for status in ReservationStatus}
    assert summary.reservations == 31

    admins = list(await db_session.scalars(select(User).where(User.role == UserRole.ADMIN)))
    assert [a.email for a in admins] == ["admin@example.edu"]
    assert await passwords.verify_password(admins[0].password_hash, PASSWORD)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 9
    timezones = set(await db_session.scalars(select(Lab.timezone)))
    assert timezones == {"America/Los_Angeles", "America/New_York", "America/Chicago"}
    restricted = set(
        await db_session.scalars(select(Server.hostname).where(Server.requires_approval))
    )
    assert restricted == {"vision-h100-01", "nlp-h100-01"}
    statuses = set(await db_session.scalars(select(Server.status)))
    assert ServerStatus.MAINTENANCE in statuses


async def test_seed_has_running_pending_and_imminent_bookings(db_session: AsyncSession) -> None:
    await _seed(db_session)

    approved = list(
        await db_session.scalars(
            select(Reservation).where(Reservation.status == ReservationStatus.APPROVED)
        )
    )
    running = [r for r in approved if r.starts_at <= NOW < r.ends_at]
    imminent = [r for r in approved if NOW < r.starts_at <= NOW + timedelta(hours=1)]
    assert len(running) == 2
    assert len(imminent) == 1
    pending = list(
        await db_session.scalars(
            select(Reservation).where(Reservation.status == ReservationStatus.PENDING)
        )
    )
    assert pending and all(r.starts_at > NOW for r in pending)
    early = await db_session.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.status == ReservationStatus.COMPLETED,
            Reservation.ends_at - Reservation.starts_at < timedelta(hours=2),
            Reservation.purpose.like("LoRA%"),
        )
    )
    assert early == 1


async def test_seeding_sends_no_email(db_session: AsyncSession) -> None:
    await _seed(db_session)

    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0


async def test_students_without_a_password_cannot_sign_in(db_session: AsyncSession) -> None:
    await _seed(db_session, student_password=None)

    student = await db_session.scalar(select(User).where(User.email == "ada.lovelace@example.edu"))
    assert student is not None
    assert not await passwords.verify_password(student.password_hash, PASSWORD)


# Every 5 hours over two weeks, so every weekday and time of day comes up, plus the
# hours around both daylight saving changes in the labs' zones.
SWEEP: Final = [
    *(datetime(2030, 1, 7, 0, 15, tzinfo=UTC) + timedelta(hours=5 * i) for i in range(68)),
    datetime(2030, 3, 10, 9, 45, tzinfo=UTC),
    datetime(2030, 3, 10, 6, 0, tzinfo=UTC),
    datetime(2030, 11, 3, 8, 30, tzinfo=UTC),
    datetime(2030, 11, 3, 5, 59, tzinfo=UTC),
]


@pytest.mark.parametrize("now", SWEEP, ids=lambda now: now.strftime("%a-%m%d-%H%M"))
async def test_seed_holds_whenever_it_runs(
    db_session: AsyncSession, cheap_hashing: None, now: datetime
) -> None:
    """Any booking rule broken at this moment would raise inside seed()."""
    summary = await _seed(db_session, now=now)

    assert set(summary.by_status) == {status.value for status in ReservationStatus}
    assert await _overlapping_active_allocations(db_session) == 0
    # Nothing is left pending or approved that should already have been expired or
    # completed by now.
    stale = await db_session.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(
            ((Reservation.status == ReservationStatus.PENDING) & (Reservation.starts_at <= now))
            | ((Reservation.status == ReservationStatus.APPROVED) & (Reservation.ends_at <= now))
        )
    )
    assert stale == 0


# --- the command ---


async def test_refuses_a_database_with_users_unless_reset(
    db_session: AsyncSession, make_settings, cheap_hashing: None
) -> None:
    settings: Settings = make_settings()
    await _seed(db_session)

    with pytest.raises(DatabaseNotEmpty):
        await run(
            settings,
            reset_first=False,
            admin_email="admin@example.edu",
            admin_password=PASSWORD,
            student_password=None,
            now=NOW,
        )

    summary = await run(
        settings,
        reset_first=True,
        admin_email="other-admin@example.edu",
        admin_password=PASSWORD,
        student_password=None,
        now=NOW,
    )
    assert summary.reservations == 31
    emails = set(await db_session.scalars(select(User.email).where(User.role == UserRole.ADMIN)))
    assert emails == {"other-admin@example.edu"}


async def test_refuses_production(make_settings) -> None:
    settings: Settings = make_settings(environment=Environment.PRODUCTION)

    with pytest.raises(CommandError, match="production"):
        await run(
            settings,
            reset_first=True,
            admin_email="admin@example.edu",
            admin_password=PASSWORD,
            student_password=None,
            now=NOW,
        )
