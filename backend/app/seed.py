"""Demo data for local development, evaluations, and the public demo deployment.

    ADMIN_PASSWORD=... SEED_STUDENT_PASSWORD=... python -m app.seed [--reset]
    ADMIN_PASSWORD=... python -m app.seed --allow-production    # the demo deployment

Creates 3 labs in different time zones, 10 servers across the GPU models (H100 servers
need approval, one server is in maintenance), the deployment's admin, a demo admin, 8
students, and about 30 reservations in every status, spread over the past and next two
weeks around now (the plan is in app.services.demo_plan).

The deployment's admin (ADMIN_EMAIL) makes no bookings and no decisions. Seeded requests
are decided by the demo admin (lab.admin@example.edu), which has no usable password.

Everything goes through the same service functions as the API, with the clock set to
when each booking or decision would have happened, so every booking rule holds:
no double booking, quotas, peak hours, the active-reservation limit. Expiry and
completion run through the real jobs. The whole seed is one transaction.

Passwords come from the environment, never from arguments or the code:
- ADMIN_PASSWORD (required), like `python -m app.cli create-admin`;
- SEED_STUDENT_PASSWORD (optional) for all students. Without it, students get a random
  password nobody knows, and only the deployment's admin can sign in.
- The demo account (DEMO_LOGIN_EMAIL and DEMO_LOGIN_PASSWORD, from the settings) gets its
  own password. It must be one of the seeded students.

Production is refused unless --allow-production is given, and even then --reset and
SEED_STUDENT_PASSWORD are refused: the seed never deletes production data, and only the
demo account gets a password anyone knows.
"""

import argparse
import asyncio
import os
import secrets
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import CommandError, read_password, validate_password
from app.core.config import Environment, Settings
from app.core.exceptions import DomainError
from app.db.base import data_tables
from app.db.session import create_engine, create_session_factory
from app.models import Notification, Reservation, User, UserRole
from app.services import demo_plan, jobs, labs, servers, users
from app.services.demo_plan import ADMIN, LABS, PLAN, SERVERS, STUDENTS

__all__ = ["LABS", "PLAN", "SERVERS", "STUDENTS", "run", "seed"]

ADMIN_EMAIL_ENV: Final = "ADMIN_EMAIL"
STUDENT_PASSWORD_ENV: Final = "SEED_STUDENT_PASSWORD"  # noqa: S105 - a variable name
DEFAULT_ADMIN_EMAIL: Final = "admin@example.edu"
ADMIN_NAME: Final = "Grace Hopper"
EXIT_OK: Final = 0
EXIT_ERROR: Final = 1


# --- seeding ---


@dataclass(frozen=True)
class SeedSummary:
    labs: int
    servers: int
    students: int
    reservations: int
    by_status: dict[str, int]


class DatabaseNotEmpty(CommandError):
    pass


async def _is_empty(session: AsyncSession) -> bool:
    return not await session.scalar(select(exists().where(User.id.is_not(None))))


async def reset(session: AsyncSession) -> None:
    """Empty every application table (not the migration version)."""
    names = data_tables()
    tables = ", ".join(f'"{name}"' for name in names)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def seed(
    session: AsyncSession,
    *,
    now: datetime,
    admin_email: str,
    admin_password: str,
    student_password: str | None,
    quota_zone: ZoneInfo,
    demo_login: tuple[str, str] | None = None,
) -> SeedSummary:
    """Create the demo data in the session's transaction. The caller commits.

    `demo_login` is (email, password) for the student that serves as the public demo
    account; it must be one of STUDENTS."""
    demo_email, demo_password = demo_login or (None, None)
    if demo_email is not None and demo_email not in {email for _, email in STUDENTS}:
        raise CommandError(
            f"The demo account {demo_email} is not one of the seeded students: "
            + ", ".join(email for _, email in STUDENTS)
        )
    # The deployment's own admin. The seed makes no bookings or decisions as it: in a
    # deployment it is a real person's account.
    await users.create_user(
        session,
        email=admin_email,
        password=admin_password,
        full_name=ADMIN_NAME,
        role=UserRole.ADMIN,
    )
    demo_admin = await demo_plan.ensure_demo_admin(session)
    students = [
        await users.create_user(
            session,
            email=email,
            password=(
                demo_password
                if email == demo_email and demo_password is not None
                else student_password or secrets.token_urlsafe(32)
            ),
            full_name=name,
        )
        for name, email in STUDENTS
    ]

    for student in students:
        # The flag that protects the shared account and lets the daily job reset it.
        student.is_demo = student.email == demo_email
        # The nightly replay books these students' plan again.
        student.seeded = True

    lab_ids: dict[str, tuple[uuid.UUID, ZoneInfo]] = {}
    for spec in LABS:
        lab = await labs.create_lab(
            session,
            name=spec.name,
            location=spec.location,
            timezone=spec.timezone,
            description=spec.description,
            is_active=True,
        )
        lab_ids[spec.key] = (lab.id, ZoneInfo(spec.timezone))

    hosts: dict[str, tuple[uuid.UUID, ZoneInfo]] = {}
    for server_spec in SERVERS:
        lab_id, zone = lab_ids[server_spec.lab]
        server = await servers.create_server(
            session,
            lab_id=lab_id,
            hostname=server_spec.hostname,
            gpu_model=server_spec.gpu_model,
            gpu_count=server_spec.gpu_count,
            cpu_cores=server_spec.cpu_cores,
            ram_gb=server_spec.ram_gb,
            interconnect=server_spec.interconnect,
            status=server_spec.status,
            requires_approval=server_spec.requires_approval,
            notes=server_spec.notes,
        )
        hosts[server_spec.hostname] = (server.id, zone)

    for booking in PLAN:
        owner = demo_admin if booking.who == ADMIN else students[booking.who]
        await demo_plan.book(
            session,
            booking,
            owner=owner,
            admin=demo_admin,
            server_id_by_host=hosts,
            now=now,
            quota_zone=quota_zone,
        )

    # What has happened by now: pending requests past their start expire, approved
    # reservations past their end complete. The same jobs the worker runs.
    await jobs.expire_pending(session, now=now)
    await jobs.complete_finished(session, now=now)
    # Seeding replays history; it should not email anyone about it. Reminders for
    # upcoming bookings are queued later by the worker as usual.
    await session.execute(delete(Notification))
    await session.flush()

    rows = await session.execute(select(Reservation.status))
    by_status: dict[str, int] = {}
    for (status,) in rows:
        by_status[status.value] = by_status.get(status.value, 0) + 1
    return SeedSummary(
        labs=len(LABS),
        servers=len(SERVERS),
        students=len(students),
        reservations=sum(by_status.values()),
        by_status=dict(sorted(by_status.items())),
    )


async def run(
    settings: Settings,
    *,
    reset_first: bool,
    admin_email: str,
    admin_password: str,
    student_password: str | None,
    now: datetime,
    allow_production: bool = False,
) -> SeedSummary:
    if settings.environment is Environment.PRODUCTION:
        if not allow_production:
            raise CommandError(
                "Refusing to seed demo data in production. For a demo deployment, add "
                "--allow-production."
            )
        if reset_first:
            raise CommandError("--reset is refused in production: it deletes every table.")
        if student_password is not None:
            raise CommandError(
                f"{STUDENT_PASSWORD_ENV} is refused in production: only the demo account "
                "gets a password anyone knows."
            )
    demo_login = None
    if settings.demo_login_email is not None and settings.demo_login_password is not None:
        demo_password = settings.demo_login_password.get_secret_value()
        validate_password(demo_password)
        demo_login = (settings.demo_login_email, demo_password)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            if reset_first:
                await reset(session)
            elif not await _is_empty(session):
                raise DatabaseNotEmpty(
                    "The database already has users. Run with --reset to replace all data."
                )
            return await seed(
                session,
                now=now,
                admin_email=admin_email,
                admin_password=admin_password,
                student_password=student_password,
                quota_zone=settings.quota_zone,
                demo_login=demo_login,
            )
    finally:
        await engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing data first. Without it, a database with users is refused.",
    )
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Seed with ENVIRONMENT=production, for a public demo. Refused with --reset.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        admin_password = read_password()
        student_password = os.environ.get(STUDENT_PASSWORD_ENV) or None
        if student_password is not None:
            validate_password(student_password)
        admin_email = users.normalize_email(os.environ.get(ADMIN_EMAIL_ENV, DEFAULT_ADMIN_EMAIL))
        settings = Settings()
        summary = asyncio.run(
            run(
                settings,
                reset_first=args.reset,
                admin_email=admin_email,
                admin_password=admin_password,
                student_password=student_password,
                now=datetime.now(UTC),
                allow_production=args.allow_production,
            )
        )
    except (CommandError, DomainError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    statuses = ", ".join(f"{count} {status}" for status, count in summary.by_status.items())
    print(
        f"Seeded {summary.labs} labs, {summary.servers} servers, 1 admin ({admin_email}), "
        f"{summary.students} students, and {summary.reservations} reservations ({statuses})."
    )
    if student_password is None:
        if settings.demo_login_email is not None:
            print(
                f"The demo account ({settings.demo_login_email}) can sign in with "
                "DEMO_LOGIN_PASSWORD. The other students cannot: they have random passwords."
            )
        else:
            print(f"Students cannot sign in: set {STUDENT_PASSWORD_ENV} to give them a password.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
