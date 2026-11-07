"""Shared setup for reservation tests: users, labs, servers, and valid booking times."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GpuModel, Lab, Server, ServerStatus, User, UserRole
from app.models.lab import DEFAULT_LAB_TIMEZONE
from app.services import labs, servers

LA = ZoneInfo(DEFAULT_LAB_TIMEZONE)
SATURDAY = 5


def next_saturday(now: datetime, at: time = time(10, 0), zone: ZoneInfo = LA) -> datetime:
    """The next Saturday at `at` local time that is still in the future (within 7 days),
    in UTC. Saturdays are off-peak, so only the rule under test can fail."""
    day = now.astimezone(zone).date()
    for offset in range(8):
        candidate = datetime.combine(day + timedelta(days=offset), at, tzinfo=zone)
        if candidate.weekday() == SATURDAY and candidate > now:
            return candidate.astimezone(UTC)
    raise AssertionError("unreachable")


def next_weekday_at(now: datetime, at: time, zone: ZoneInfo = LA) -> datetime:
    """The next Monday to Friday at `at` local time, at least an hour from now."""
    day = now.astimezone(zone).date()
    for offset in range(8):
        candidate = datetime.combine(day + timedelta(days=offset), at, tzinfo=zone)
        if candidate.weekday() < SATURDAY and candidate > now + timedelta(hours=1):
            return candidate.astimezone(UTC)
    raise AssertionError("unreachable")


async def add_user(
    db: AsyncSession,
    email: str,
    *,
    role: UserRole = UserRole.STUDENT,
    quota: int = 48,
) -> User:
    """A user inserted directly, without password hashing, for tests that call
    services and never sign in."""
    user = User(
        email=email,
        password_hash="unused",
        full_name=email.split("@")[0],
        role=role,
        weekly_gpu_hour_quota=quota,
    )
    db.add(user)
    await db.commit()
    return user


async def add_lab(db: AsyncSession, name: str = "Vision Lab", **fields: Any) -> Lab:
    values: dict[str, Any] = {
        "name": name,
        "location": "Building 3",
        "timezone": DEFAULT_LAB_TIMEZONE,
        "description": None,
        "is_active": True,
    }
    values.update(fields)
    lab = await labs.create_lab(db, **values)
    await db.commit()
    return lab


async def add_server(
    db: AsyncSession,
    lab: Lab,
    hostname: str = "a100-node-01",
    *,
    gpu_count: int = 4,
    status: ServerStatus = ServerStatus.AVAILABLE,
    requires_approval: bool = False,
    max_booking_hours: int = 12,
) -> Server:
    server = await servers.create_server(
        db,
        lab_id=lab.id,
        hostname=hostname,
        gpu_model=GpuModel.A100_80GB,
        gpu_count=gpu_count,
        cpu_cores=64,
        ram_gb=512,
        status=status,
        requires_approval=requires_approval,
        max_booking_hours=max_booking_hours,
    )
    await db.commit()
    return server


def booking_body(
    server_id: uuid.UUID | str,
    start: datetime,
    hours: float = 2,
    gpu_count: int = 1,
    purpose: str = "Fine-tuning a 7B model",
) -> dict[str, Any]:
    return {
        "server_id": str(server_id),
        "gpu_count": gpu_count,
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=hours)).isoformat(),
        "purpose": purpose,
    }
