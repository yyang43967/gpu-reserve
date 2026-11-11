"""Demo data for local development, evaluations, and the public demo deployment.

    ADMIN_PASSWORD=... SEED_STUDENT_PASSWORD=... python -m app.seed [--reset]
    ADMIN_PASSWORD=... python -m app.seed --allow-production    # the demo deployment

Creates 3 labs in different time zones, 10 servers across the GPU models (H100 servers
need approval, one server is in maintenance), 1 admin, 8 students, and about 30
reservations in every status, spread over the past and next two weeks around now.

Everything goes through the same service functions as the API, with the clock set to
when each booking or decision would have happened, so every booking rule holds:
no double booking, quotas, peak hours, the active-reservation limit. Expiry and
completion run through the real jobs. The whole seed is one transaction.

Passwords come from the environment, never from arguments or the code:
- ADMIN_PASSWORD (required), like `python -m app.cli create-admin`;
- SEED_STUDENT_PASSWORD (optional) for all students. Without it, students get a random
  password nobody knows, and only the admin can sign in.
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
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cli import CommandError, read_password, validate_password
from app.core.config import Environment, Settings
from app.core.exceptions import DomainError
from app.db.base import data_tables
from app.db.session import create_engine, create_session_factory
from app.models import GpuModel, Notification, Reservation, ServerStatus, User, UserRole
from app.services import jobs, labs, reservations, servers, users
from app.services.booking_rules import floor_to_grid
from app.services.reservations import NewReservation

ADMIN_EMAIL_ENV: Final = "ADMIN_EMAIL"
STUDENT_PASSWORD_ENV: Final = "SEED_STUDENT_PASSWORD"  # noqa: S105 - a variable name
DEFAULT_ADMIN_EMAIL: Final = "admin@example.edu"
ADMIN_NAME: Final = "Grace Hopper"
EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
# Past bookings are made this long before they start, and decided an hour later.
BOOKED_AHEAD: Final = timedelta(days=2)
DECISION_DELAY: Final = timedelta(hours=1)


# --- the data ---


@dataclass(frozen=True)
class LabSpec:
    key: str
    name: str
    location: str
    timezone: str
    description: str


@dataclass(frozen=True)
class ServerSpec:
    lab: str
    hostname: str
    gpu_model: GpuModel
    gpu_count: int
    cpu_cores: int
    ram_gb: int
    interconnect: str | None = None
    requires_approval: bool = False
    status: ServerStatus = ServerStatus.AVAILABLE
    notes: str | None = None


LABS: Final = (
    LabSpec(
        "vision",
        "Vision Lab",
        "Gates Building, Room 104",
        "America/Los_Angeles",
        "Computer vision and multimodal models.",
    ),
    LabSpec(
        "nlp",
        "NLP Lab",
        "Hamilton Hall, Room 210",
        "America/New_York",
        "Language models, retrieval, and evaluation.",
    ),
    LabSpec(
        "robotics",
        "Robotics Lab",
        "Engineering Annex, Bay 3",
        "America/Chicago",
        "Simulation, reinforcement learning, and control.",
    ),
)

SERVERS: Final = (
    ServerSpec("vision", "vision-a100-01", GpuModel.A100_80GB, 8, 128, 1024, "NVLink"),
    ServerSpec("vision", "vision-a100-02", GpuModel.A100_40GB, 4, 64, 512),
    ServerSpec("vision", "vision-4090-01", GpuModel.RTX_4090, 4, 32, 256),
    ServerSpec(
        "vision",
        "vision-h100-01",
        GpuModel.H100_80GB,
        8,
        192,
        2048,
        "NVLink",
        requires_approval=True,
        notes="Reserved mainly for large training runs. Explain the job in the purpose.",
    ),
    ServerSpec(
        "nlp",
        "nlp-h100-01",
        GpuModel.H100_80GB,
        8,
        192,
        2048,
        "NVLink",
        requires_approval=True,
    ),
    ServerSpec("nlp", "nlp-a100-01", GpuModel.A100_80GB, 4, 64, 512, "NVLink"),
    ServerSpec("nlp", "nlp-l40s-01", GpuModel.L40S, 4, 64, 384),
    ServerSpec("robotics", "robo-6000-01", GpuModel.RTX_6000_ADA, 2, 32, 256),
    ServerSpec("robotics", "robo-4090-01", GpuModel.RTX_4090, 2, 24, 128),
    ServerSpec(
        "robotics",
        "robo-l40s-01",
        GpuModel.L40S,
        2,
        32,
        256,
        status=ServerStatus.MAINTENANCE,
        notes="Waiting for a replacement power supply.",
    ),
)

STUDENTS: Final = (
    ("Ada Lovelace", "ada.lovelace@example.edu"),
    ("Alan Turing", "alan.turing@example.edu"),
    ("Katherine Johnson", "katherine.johnson@example.edu"),
    ("Claude Shannon", "claude.shannon@example.edu"),
    ("Barbara Liskov", "barbara.liskov@example.edu"),
    ("Donald Knuth", "donald.knuth@example.edu"),
    ("Frances Allen", "frances.allen@example.edu"),
    ("John McCarthy", "john.mccarthy@example.edu"),
)
ADMIN: Final = -1  # stands for the admin in the booking plan


class Outcome(StrEnum):
    # Leave as created: approved (or pending on restricted servers). The jobs then
    # complete or expire it if it is in the past.
    KEEP = "keep"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"  # by the owner, before the start
    ADMIN_CANCEL = "admin_cancel"
    END_EARLY = "end_early"  # by the owner, 50 minutes in


@dataclass(frozen=True)
class Booking:
    """One reservation. `day`/`hour` is a wall time in the server's lab, `day` days
    from today there. With `hours_from_now` instead, it starts that many hours from
    the current half hour (for running and imminent bookings)."""

    who: int
    hostname: str
    hours: int
    gpus: int
    purpose: str
    day: int = 0
    hour: int = 0
    hours_from_now: float | None = None
    outcome: Outcome = Outcome.KEEP
    reason: str | None = None


H100_REASON: Final = "The H100s are reserved for the course project deadline that week."

PLAN: Final = (
    # The past two weeks.
    Booking(
        0, "vision-a100-01", 4, 4, "Fine-tuning a ViT-L on the lab's medical imaging set", -12, 19
    ),
    Booking(
        1, "vision-a100-01", 3, 8, "Pretraining run for a 1B-parameter vision encoder", -10, 20
    ),
    Booking(
        2,
        "nlp-a100-01",
        2,
        2,
        "LoRA fine-tune of a 7B model for summarization",
        -9,
        10,
        outcome=Outcome.END_EARLY,
    ),
    Booking(
        3,
        "vision-h100-01",
        3,
        4,
        "Video diffusion ablations for the CVPR submission",
        -8,
        19,
        outcome=Outcome.APPROVE,
    ),
    Booking(
        4,
        "vision-h100-01",
        2,
        2,
        "Benchmarking FlashAttention kernels",
        -7,
        19,
        outcome=Outcome.REJECT,
        reason=H100_REASON,
    ),
    Booking(5, "nlp-h100-01", 3, 8, "Full fine-tune of a 13B model on instruction data", -6, 20),
    Booking(
        6,
        "vision-4090-01",
        2,
        2,
        "Training a small segmentation model",
        -5,
        13,
        outcome=Outcome.CANCEL,
    ),
    Booking(7, "robo-6000-01", 4, 1, "Simulated grasping policy training (PPO)", -4, 18),
    Booking(
        0,
        "nlp-l40s-01",
        2,
        4,
        "Embedding the course reading list for retrieval experiments",
        -3,
        21,
    ),
    Booking(1, "vision-a100-02", 1, 4, "Inference throughput test for the demo", -2, 9),
    Booking(2, "robo-4090-01", 3, 2, "Domain randomization sweep", -1, 20),
    Booking(
        3,
        "vision-a100-02",
        2,
        2,
        "Feature extraction for a reading group",
        -1,
        19,
        outcome=Outcome.ADMIN_CANCEL,
    ),
    # Running now, and starting within the hour.
    Booking(
        4,
        "vision-a100-01",
        3,
        1,
        "Evaluating checkpoints on the validation split",
        hours_from_now=-1,
    ),
    Booking(5, "nlp-a100-01", 4, 1, "RAG retrieval evaluation, top-k sweep", hours_from_now=-2),
    Booking(
        6,
        "vision-4090-01",
        2,
        1,
        "Quick hyperparameter search for a CNN baseline",
        hours_from_now=0.5,
    ),
    # The next two weeks.
    Booking(7, "vision-h100-01", 3, 4, "Training a world model for the robot arm", 2, 19),
    Booking(0, "nlp-h100-01", 2, 8, "DPO training run on preference data", 3, 20),
    Booking(
        1,
        "vision-h100-01",
        2,
        2,
        "Distillation of a large vision-language model",
        4,
        19,
        outcome=Outcome.APPROVE,
    ),
    Booking(
        2,
        "nlp-h100-01",
        2,
        2,
        "Long-context attention benchmark",
        5,
        19,
        outcome=Outcome.REJECT,
        reason=H100_REASON,
    ),
    Booking(3, "vision-a100-01", 4, 8, "Multi-node data-parallel training test", 1, 19),
    Booking(4, "nlp-l40s-01", 3, 4, "Batch inference for the annotation study", 2, 20),
    Booking(5, "robo-6000-01", 2, 2, "Sim-to-real transfer experiments", 3, 19),
    Booking(6, "vision-a100-02", 3, 4, "Training a detection model on the campus dataset", 6, 20),
    Booking(
        7,
        "robo-4090-01",
        2,
        1,
        "Rendering synthetic training scenes",
        7,
        19,
        outcome=Outcome.CANCEL,
    ),
    Booking(0, "vision-4090-01", 4, 4, "Stable diffusion fine-tune for the art project", 8, 19),
    Booking(1, "nlp-a100-01", 3, 4, "Evaluating quantized 70B models", 9, 19),
    Booking(2, "vision-a100-01", 2, 4, "Reproducing a paper's baseline", 10, 20),
    Booking(3, "nlp-l40s-01", 3, 2, "Speech recognition fine-tune", 11, 19),
    Booking(ADMIN, "vision-a100-02", 2, 2, "Driver and CUDA upgrade validation", 4, 20),
    Booking(4, "robo-6000-01", 2, 1, "Policy evaluation in simulation", 12, 19),
    Booking(5, "vision-4090-01", 1, 2, "Profiling a data loader", 13, 19),
)


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


def _start(booking: Booking, zone: ZoneInfo, now: datetime) -> datetime:
    if booking.hours_from_now is not None:
        return floor_to_grid(now) + timedelta(hours=booking.hours_from_now)
    day = now.astimezone(zone).date() + timedelta(days=booking.day)
    return datetime.combine(day, time(booking.hour), tzinfo=zone).astimezone(UTC)


async def _book(
    session: AsyncSession,
    booking: Booking,
    *,
    owner: User,
    admin: User,
    server_id_by_host: dict[str, tuple[uuid.UUID, ZoneInfo]],
    now: datetime,
    quota_zone: ZoneInfo,
) -> Reservation:
    server_id, zone = server_id_by_host[booking.hostname]
    start = _start(booking, zone, now)
    end = start + timedelta(hours=booking.hours)
    # Past bookings were made two days ahead; current and future ones just now.
    booked_at = min(start - BOOKED_AHEAD, now - timedelta(minutes=1))
    decided_at = min(booked_at + DECISION_DELAY, now)
    reservation = await reservations.create_reservation(
        session,
        owner,
        NewReservation(server_id, booking.gpus, start, end, booking.purpose),
        now=booked_at,
        quota_zone=quota_zone,
    )
    match booking.outcome:
        case Outcome.APPROVE:
            await reservations.approve_reservation(session, admin, reservation.id, now=decided_at)
        case Outcome.REJECT:
            await reservations.reject_reservation(
                session, admin, reservation.id, reason=booking.reason or "", now=decided_at
            )
        case Outcome.CANCEL:
            await reservations.cancel_reservation(session, owner, reservation.id, now=decided_at)
        case Outcome.ADMIN_CANCEL:
            await reservations.cancel_reservation(session, admin, reservation.id, now=decided_at)
        case Outcome.END_EARLY:
            await reservations.cancel_reservation(
                session, owner, reservation.id, now=start + timedelta(minutes=50)
            )
        case Outcome.KEEP:
            pass
    return reservation


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
    admin = await users.create_user(
        session,
        email=admin_email,
        password=admin_password,
        full_name=ADMIN_NAME,
        role=UserRole.ADMIN,
    )
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
        owner = admin if booking.who == ADMIN else students[booking.who]
        await _book(
            session,
            booking,
            owner=owner,
            admin=admin,
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
        summary = asyncio.run(
            run(
                Settings(),
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
        print(f"Students cannot sign in: set {STUDENT_PASSWORD_ENV} to give them a password.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
