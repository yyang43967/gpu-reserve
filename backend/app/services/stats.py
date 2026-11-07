"""Dashboard numbers and per-server utilization.

Utilization compares GPU-hours booked with GPU-hours that existed. "Existed" uses each
server's current GPU count and status for the whole window, even a past one: there is
no history of capacity changes, so a server that grew from 4 to 8 GPUs last week is
counted as 8 for all of it. That approximation is accepted for a dashboard.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Float, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.exceptions import InvalidUtilizationWindow
from app.models import (
    Gpu,
    GpuAllocation,
    Lab,
    Reservation,
    ReservationStatus,
    Server,
    ServerStatus,
    User,
    UserRole,
)
from app.models.reservation import HOLDING_STATUSES
from app.services import booking_rules, labs, reservations
from app.services.booking_rules import Interval
from app.services.reservations import QuotaStatus

DEFAULT_UTILIZATION_WINDOW: Final = timedelta(days=7)
MAX_UTILIZATION_WINDOW: Final = timedelta(days=31)
UPCOMING_LIMIT: Final = 3
SECONDS_PER_HOUR: Final = 3600
# Booked time is what was approved and used; pending requests are shown separately.
BOOKED_STATUSES: Final = (ReservationStatus.APPROVED, ReservationStatus.COMPLETED)


# --- utilization ---


@dataclass(frozen=True)
class UtilizationTotals:
    available_gpu_hours: float
    booked_gpu_hours: float
    pending_gpu_hours: float

    @property
    def utilization(self) -> float:
        if self.available_gpu_hours == 0:
            return 0.0
        return self.booked_gpu_hours / self.available_gpu_hours


@dataclass(frozen=True)
class ServerUtilization(UtilizationTotals):
    server: Server

    @property
    def hostname(self) -> str:
        return self.server.hostname


@dataclass(frozen=True)
class Utilization:
    window: Interval
    servers: list[ServerUtilization]
    totals: UtilizationTotals


def utilization_window(start: datetime | None, end: datetime | None, *, now: datetime) -> Interval:
    """The requested window, defaulting to the seven days before now."""
    window_end = end or now
    window_start = start or window_end - DEFAULT_UTILIZATION_WINDOW
    if window_end <= window_start:
        raise InvalidUtilizationWindow("`to` must be after `from`.")
    if window_end - window_start > MAX_UTILIZATION_WINDOW:
        raise InvalidUtilizationWindow(
            f"The window can be at most {MAX_UTILIZATION_WINDOW.days} days long."
        )
    return Interval(window_start, window_end)


def _overlap_hours(window: Interval) -> ColumnElement[float]:
    """Hours of an allocation inside the window, as a SQL expression."""
    seconds = func.extract(
        "epoch",
        func.least(GpuAllocation.ends_at, window.end)
        - func.greatest(GpuAllocation.starts_at, window.start),
    )
    return cast(seconds / SECONDS_PER_HOUR, Float)


async def _hours_by_server(
    session: AsyncSession, server_ids: list[uuid.UUID], window: Interval
) -> dict[uuid.UUID, tuple[float, float]]:
    """(booked, pending) GPU-hours per server inside the window. Each allocation is one
    GPU, so summing allocation time gives GPU-hours. Allocations of ended-early
    reservations were shortened, so only the time used counts. Removed GPUs still
    count: their bookings happened."""
    hours = _overlap_hours(window)
    rows = await session.execute(
        select(
            Gpu.server_id,
            func.coalesce(
                func.sum(case((Reservation.status.in_(BOOKED_STATUSES), hours), else_=0.0)), 0.0
            ),
            func.coalesce(
                func.sum(case((Reservation.status == ReservationStatus.PENDING, hours), else_=0.0)),
                0.0,
            ),
        )
        .select_from(GpuAllocation)
        .join(Gpu, Gpu.id == GpuAllocation.gpu_id)
        .join(Reservation, Reservation.id == GpuAllocation.reservation_id)
        .where(
            Gpu.server_id.in_(server_ids),
            GpuAllocation.starts_at < window.end,
            GpuAllocation.ends_at > window.start,
            Reservation.status.in_((*BOOKED_STATUSES, ReservationStatus.PENDING)),
        )
        .group_by(Gpu.server_id)
    )
    return {row[0]: (float(row[1]), float(row[2])) for row in rows}


async def utilization(
    session: AsyncSession,
    viewer: User,
    *,
    window: Interval,
    lab_id: uuid.UUID | None = None,
) -> Utilization:
    """Booked and pending GPU-hours against available GPU-hours, per server that is not
    retired, optionally in one lab. Servers in maintenance are included: their time
    could not be booked, which is worth seeing."""
    conditions = [Server.status != ServerStatus.RETIRED]
    if lab_id is not None:
        await labs.get_lab(session, viewer, lab_id)  # LabNotFound for an unknown lab
        conditions.append(Server.lab_id == lab_id)
    servers = list(
        await session.scalars(
            select(Server)
            .join(Server.lab)
            .where(*conditions)
            .options(contains_eager(Server.lab))
            .order_by(Lab.name, Server.hostname)
        )
    )
    hours = await _hours_by_server(session, [s.id for s in servers], window)
    window_hours = (window.end - window.start).total_seconds() / SECONDS_PER_HOUR
    rows = [
        ServerUtilization(
            server=server,
            available_gpu_hours=server.gpu_count * window_hours,
            booked_gpu_hours=hours.get(server.id, (0.0, 0.0))[0],
            pending_gpu_hours=hours.get(server.id, (0.0, 0.0))[1],
        )
        for server in servers
    ]
    totals = UtilizationTotals(
        available_gpu_hours=sum(r.available_gpu_hours for r in rows),
        booked_gpu_hours=sum(r.booked_gpu_hours for r in rows),
        pending_gpu_hours=sum(r.pending_gpu_hours for r in rows),
    )
    return Utilization(window=window, servers=rows, totals=totals)


# --- overview ---


@dataclass(frozen=True)
class AdminOverview:
    pending_approvals: int
    # GPUs held right now by approved reservations.
    gpus_in_use: int
    # GPUs on servers that are not retired.
    gpus_total: int
    week: Interval
    utilization_this_week: float


@dataclass(frozen=True)
class Overview:
    quota: QuotaStatus
    # The user's next pending or approved reservations that have not ended, soonest
    # first. A running one comes first.
    upcoming: list[Reservation]
    my_pending: int
    admin: AdminOverview | None


async def _upcoming(session: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[Reservation]:
    rows = await session.scalars(
        select(Reservation)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(HOLDING_STATUSES),
            Reservation.ends_at > now,
        )
        .options(
            selectinload(Reservation.server).selectinload(Server.lab),
            selectinload(Reservation.user),
            selectinload(Reservation.allocations).selectinload(GpuAllocation.gpu),
        )
        .order_by(Reservation.starts_at, Reservation.id)
        .limit(UPCOMING_LIMIT)
    )
    return list(rows)


async def _count(session: AsyncSession, *conditions: object) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Reservation).where(*conditions)  # type: ignore[arg-type]
    )
    return count or 0


async def _admin_overview(
    session: AsyncSession, viewer: User, *, now: datetime, quota_zone: ZoneInfo
) -> AdminOverview:
    in_use = await session.scalar(
        select(func.count())
        .select_from(GpuAllocation)
        .join(Reservation, Reservation.id == GpuAllocation.reservation_id)
        .where(
            GpuAllocation.active.is_(True),
            Reservation.status == ReservationStatus.APPROVED,
            and_(GpuAllocation.starts_at <= now, GpuAllocation.ends_at > now),
        )
    )
    total = await session.scalar(
        select(func.coalesce(func.sum(Server.gpu_count), 0)).where(
            Server.status != ServerStatus.RETIRED
        )
    )
    week = booking_rules.week_containing(now, quota_zone)
    report = await utilization(session, viewer, window=week)
    return AdminOverview(
        pending_approvals=await _count(session, Reservation.status == ReservationStatus.PENDING),
        gpus_in_use=in_use or 0,
        gpus_total=int(total or 0),
        week=week,
        utilization_this_week=report.totals.utilization,
    )


async def overview(
    session: AsyncSession, viewer: User, *, now: datetime, quota_zone: ZoneInfo
) -> Overview:
    """What the dashboard shows. Every user sees their own quota, next bookings, and
    pending requests; admins also see the approval queue and how busy the GPUs are."""
    return Overview(
        quota=await reservations.quota_status(session, viewer, at=now, quota_zone=quota_zone),
        upcoming=await _upcoming(session, viewer.id, now),
        my_pending=await _count(
            session,
            Reservation.user_id == viewer.id,
            Reservation.status == ReservationStatus.PENDING,
        ),
        admin=(
            await _admin_overview(session, viewer, now=now, quota_zone=quota_zone)
            if viewer.role is UserRole.ADMIN
            else None
        ),
    )
