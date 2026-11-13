"""The demo data seed: it must hold at any moment it is run."""

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import CommandError
from app.core.config import Environment, Settings
from app.core.security import UNUSABLE_PASSWORD_HASH
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
from app.services import demo_plan, passwords, users
from app.services.demo_plan import DEMO_ADMIN_EMAIL, DemoAdminConflict
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

    admins = {
        admin.email: admin
        for admin in await db_session.scalars(select(User).where(User.role == UserRole.ADMIN))
    }
    assert set(admins) == {"admin@example.edu", DEMO_ADMIN_EMAIL}
    assert await passwords.verify_password(admins["admin@example.edu"].password_hash, PASSWORD)
    assert await db_session.scalar(select(func.count()).select_from(User)) == 10
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


async def test_seed_marks_its_people_and_bookings_but_not_the_deployment_admin(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)

    users = (await db_session.execute(select(User.email, User.role, User.seeded))).all()
    assert {(email, seeded) for email, role, seeded in users if role is UserRole.ADMIN} == {
        ("admin@example.edu", False),
        (DEMO_ADMIN_EMAIL, True),
    }
    assert all(seeded for _, role, seeded in users if role is UserRole.STUDENT)
    owners = set(
        await db_session.scalars(
            select(User.email).join(Reservation, Reservation.user_id == User.id)
        )
    )
    assert "admin@example.edu" not in owners
    assert set(await db_session.scalars(select(Reservation.seeded))) == {True}
    # Every decision on a seeded request was made by the demo admin.
    deciders = set(
        await db_session.scalars(
            select(User.email).join(Reservation, Reservation.decided_by == User.id).distinct()
        )
    )
    assert deciders == {DEMO_ADMIN_EMAIL}


async def test_nobody_can_sign_in_as_the_demo_admin(db_session: AsyncSession) -> None:
    await _seed(db_session)

    demo_admin = await db_session.scalar(select(User).where(User.email == DEMO_ADMIN_EMAIL))
    assert demo_admin is not None
    assert demo_admin.password_hash == UNUSABLE_PASSWORD_HASH
    for guess in ("", "!", PASSWORD, UNUSABLE_PASSWORD_HASH):
        assert not await passwords.verify_password(demo_admin.password_hash, guess)


async def test_the_demo_admin_never_takes_over_an_existing_account(
    db_session: AsyncSession,
) -> None:
    await users.create_user(
        db_session, email=DEMO_ADMIN_EMAIL, password=PASSWORD, full_name="Someone Else"
    )

    with pytest.raises(DemoAdminConflict):
        await demo_plan.ensure_demo_admin(db_session)
    account = await db_session.scalar(select(User).where(User.email == DEMO_ADMIN_EMAIL))
    assert account is not None
    assert (account.role, account.seeded) == (UserRole.STUDENT, False)


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
    assert emails == {"other-admin@example.edu", DEMO_ADMIN_EMAIL}


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


DEMO_EMAIL: Final = "ada.lovelace@example.edu"
DEMO_PASSWORD: Final = "demo-password-shown-on-the-page"


def _demo_settings(make_settings, **overrides) -> Settings:
    settings: Settings = make_settings(
        demo_login_email=DEMO_EMAIL, demo_login_password=DEMO_PASSWORD, **overrides
    )
    return settings


async def test_production_needs_allow_production_and_gives_only_the_demo_a_password(
    db_session: AsyncSession, make_settings, cheap_hashing: None
) -> None:
    settings = _demo_settings(make_settings, environment=Environment.PRODUCTION)

    summary = await run(
        settings,
        reset_first=False,
        admin_email="admin@example.edu",
        admin_password=PASSWORD,
        student_password=None,
        now=NOW,
        allow_production=True,
    )

    assert summary.reservations == 31
    rows = (await db_session.execute(select(User.email, User.password_hash))).all()
    hashes = {email: password_hash for email, password_hash in rows}
    # cheap_hashing stores "plain:<password>".
    assert hashes.pop(DEMO_EMAIL) == f"plain:{DEMO_PASSWORD}"
    assert hashes.pop("admin@example.edu") == f"plain:{PASSWORD}"
    assert hashes.pop(DEMO_ADMIN_EMAIL) == UNUSABLE_PASSWORD_HASH
    assert len(hashes) == 7
    # The other students got random passwords: none shared, none known.
    assert len(set(hashes.values())) == 7
    assert f"plain:{DEMO_PASSWORD}" not in hashes.values()
    flagged = set(await db_session.scalars(select(User.email).where(User.is_demo)))
    assert flagged == {DEMO_EMAIL}


@pytest.mark.parametrize(
    ("reset_first", "student_password", "message"),
    [(True, None, "--reset"), (False, PASSWORD, "SEED_STUDENT_PASSWORD")],
)
async def test_production_refuses_reset_and_shared_student_passwords(
    db_session: AsyncSession,
    make_settings,
    reset_first: bool,
    student_password: str | None,
    message: str,
) -> None:
    settings = _demo_settings(make_settings, environment=Environment.PRODUCTION)

    with pytest.raises(CommandError, match=message):
        await run(
            settings,
            reset_first=reset_first,
            admin_email="admin@example.edu",
            admin_password=PASSWORD,
            student_password=student_password,
            now=NOW,
            allow_production=True,
        )
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0


async def test_the_demo_account_must_be_a_seeded_student(
    db_session: AsyncSession, make_settings, cheap_hashing: None
) -> None:
    settings: Settings = make_settings(
        demo_login_email="someone@example.edu", demo_login_password=DEMO_PASSWORD
    )

    with pytest.raises(CommandError, match="not one of the seeded students"):
        await run(
            settings,
            reset_first=False,
            admin_email="admin@example.edu",
            admin_password=PASSWORD,
            student_password=None,
            now=NOW,
        )
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0


def test_the_command_says_the_demo_account_can_sign_in(
    migrated_database_url: str,
    clean_database: None,
    cheap_hashing: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.seed import EXIT_OK, main

    monkeypatch.setenv("DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("SEED_STUDENT_PASSWORD", raising=False)
    monkeypatch.setenv("DEMO_LOGIN_EMAIL", DEMO_EMAIL)
    monkeypatch.setenv("DEMO_LOGIN_PASSWORD", DEMO_PASSWORD)

    assert main([]) == EXIT_OK

    out = capsys.readouterr().out
    assert f"The demo account ({DEMO_EMAIL}) can sign in" in out
    assert "The other students cannot" in out
    assert "Students cannot sign in" not in out
    assert DEMO_PASSWORD not in out
