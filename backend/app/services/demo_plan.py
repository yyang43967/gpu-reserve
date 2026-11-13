"""The demo data plan, shared by the one-off seed (app.seed) and the nightly replay.

Labs, servers, seeded people, and about 30 bookings described relative to "now", so
the same plan can be placed around any moment. Every booking goes through the same
service functions as the API, with the clock set to when it would have happened, so
every rule holds against whatever else is in the database.

Seeded people are flagged (`users.seeded`), and so is every booking made from the plan
(`reservations.seeded`). Decisions on seeded requests are made by a seeded demo admin
with no usable password, never by a deployment's real admin account.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import UNUSABLE_PASSWORD_HASH
from app.models import GpuModel, Reservation, ServerStatus, User, UserRole
from app.services import reservations, users
from app.services.booking_rules import floor_to_grid
from app.services.reservations import NewReservation

# Past bookings are made this long before they start, and decided an hour later.
BOOKED_AHEAD: Final = timedelta(days=2)
DECISION_DELAY: Final = timedelta(hours=1)

DEMO_ADMIN_NAME: Final = "Lab Admin"
DEMO_ADMIN_EMAIL: Final = "lab.admin@example.edu"


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
ADMIN: Final = -1  # stands for the demo admin in the booking plan


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


# --- booking ---


class DemoAdminConflict(Exception):
    """The demo admin's address belongs to an account the seed did not create."""


async def ensure_demo_admin(session: AsyncSession) -> User:
    """The seeded admin that decides seeded requests, created if missing.

    It has no usable password (nobody can sign in as it). An existing account with the
    same address that the seed did not create is never taken over."""
    existing = await users.get_user_by_email(session, DEMO_ADMIN_EMAIL)
    if existing is not None:
        if not (existing.seeded and existing.role is UserRole.ADMIN):
            raise DemoAdminConflict(
                f"{DEMO_ADMIN_EMAIL} belongs to an account the demo seed did not create."
            )
        return existing
    admin = await users.create_user(
        session,
        email=DEMO_ADMIN_EMAIL,
        # Replaced below; create_user always hashes something.
        password=uuid.uuid4().hex,
        full_name=DEMO_ADMIN_NAME,
        role=UserRole.ADMIN,
    )
    admin.password_hash = UNUSABLE_PASSWORD_HASH
    admin.seeded = True
    await session.flush()
    return admin


def start_of(booking: Booking, zone: ZoneInfo, now: datetime) -> datetime:
    """When a plan entry starts, in UTC, for a plan placed around `now`."""
    if booking.hours_from_now is not None:
        return floor_to_grid(now) + timedelta(hours=booking.hours_from_now)
    day = now.astimezone(zone).date() + timedelta(days=booking.day)
    return datetime.combine(day, time(booking.hour), tzinfo=zone).astimezone(UTC)


async def book(
    session: AsyncSession,
    booking: Booking,
    *,
    owner: User,
    admin: User,
    server_id_by_host: dict[str, tuple[uuid.UUID, ZoneInfo]],
    now: datetime,
    quota_zone: ZoneInfo,
) -> Reservation:
    """Book one plan entry as `owner`, then apply its outcome with `admin` deciding,
    with the clock set to when each step would have happened. Raises a DomainError
    for any rule the booking breaks against the data as it is."""
    server_id, zone = server_id_by_host[booking.hostname]
    start = start_of(booking, zone, now)
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
    # Flushed at once: the decisions below reload the row and would overwrite it.
    reservation.seeded = True
    await session.flush()
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
