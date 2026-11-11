"""Finding times that fit a job: N GPUs (optionally of a model or memory size) for a
duration, somewhere in a window.

The search only advises. create_reservation stays the authority, so a result can still
be refused if someone books it first. The same function serves the web form and, later,
the assistant's find_slots tool, so it knows nothing about HTTP.

Window semantics: every result starts at or after `earliest` and ends at or before
`latest`. Starts are on the 30-minute grid, strictly after now, and at most 14 days
ahead, like any booking.
"""

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.core.exceptions import (
    InvalidSearchWindow,
    PeakHoursLimit,
    QuotaExceeded,
    TooManyReservations,
)
from app.models import GpuModel, Lab, Server, ServerStatus, User
from app.services import availability_cache, booking_rules, reservations
from app.services.booking_rules import BOOKING_HORIZON, SLOT, Booking, Interval

MAX_PER_SERVER: Final = 3
MAX_RESULTS: Final = 20
MAX_SEARCH_WINDOW: Final = timedelta(days=14)


@dataclass(frozen=True)
class SlotQuery:
    gpu_count: int
    duration_minutes: int
    earliest: datetime
    latest: datetime
    gpu_model: GpuModel | None = None
    min_gpu_memory_gb: int | None = None


@dataclass(frozen=True)
class SlotCandidate:
    server: Server
    starts_at: datetime
    ends_at: datetime
    # The fewest GPUs free in any slot of the candidate, the requested ones included.
    free_gpus: int


# --- pure helpers ---


def feasible_starts(free: Sequence[int], *, needed: int, length: int) -> list[tuple[int, int]]:
    """(index, fewest free) for every run of `length` slots whose every slot has at
    least `needed` free GPUs. A sliding-window minimum, linear in the slots."""
    result: list[tuple[int, int]] = []
    window: deque[int] = deque()  # indexes whose values increase from front to back
    for i, value in enumerate(free):
        while window and free[window[-1]] >= value:
            window.pop()
        window.append(i)
        start = i - length + 1
        if window[0] < start:
            window.popleft()
        if start >= 0 and free[window[0]] >= needed:
            result.append((start, free[window[0]]))
    return result


def spread_out(starts: Sequence[int], *, length: int, limit: int) -> list[int]:
    """Pick up to `limit` starts, earliest first, none overlapping the one before."""
    picked: list[int] = []
    next_free = None
    for start in starts:
        if next_free is None or start >= next_free:
            picked.append(start)
            next_free = start + length
            if len(picked) == limit:
                break
    return picked


def _rank(candidate: SlotCandidate, gpu_count: int) -> tuple[object, ...]:
    """Earliest start, then no approval needed, then the smallest GPUs that fit (keep
    the big ones for jobs that need them), then the tightest fit (fewest GPUs left
    free beside the booking), then hostname for a stable order."""
    server = candidate.server
    return (
        candidate.starts_at,
        server.requires_approval,
        server.gpu_memory_gb,
        candidate.free_gpus - gpu_count,
        server.hostname,
    )


# --- the search ---


def _effective_window(query: SlotQuery, now: datetime) -> Interval:
    """The window shrunk to grid boundaries: starts no earlier than the first boundary
    after now, ends no later than the last boundary before `latest`."""
    earliest = max(query.earliest, now)
    start = booking_rules.ceil_to_grid(earliest)
    if start <= now:
        start += SLOT
    end = booking_rules.floor_to_grid(query.latest)
    return Interval(start, end)


def validate(query: SlotQuery, now: datetime) -> Interval:
    if query.latest - query.earliest > MAX_SEARCH_WINDOW:
        raise InvalidSearchWindow(
            f"The search window can be at most {MAX_SEARCH_WINDOW.days} days long."
        )
    window = _effective_window(query, now)
    if window.end - window.start < timedelta(minutes=query.duration_minutes):
        raise InvalidSearchWindow(
            "The window is shorter than the job, once times in the past are left out."
        )
    return window


async def _candidate_servers(session: AsyncSession, query: SlotQuery) -> list[Server]:
    """Servers open for booking that can hold the job. Bookable is stricter than
    visible, so this is the same for students and admins."""
    conditions = [
        Server.status == ServerStatus.AVAILABLE,
        Lab.is_active.is_(True),
        Server.gpu_count >= query.gpu_count,
    ]
    if query.gpu_model is not None:
        conditions.append(Server.gpu_model == query.gpu_model)
    if query.min_gpu_memory_gb is not None:
        conditions.append(Server.gpu_memory_gb >= query.min_gpu_memory_gb)
    rows = await session.scalars(
        select(Server)
        .join(Server.lab)
        .where(*conditions)
        .options(contains_eager(Server.lab))
        .order_by(Server.hostname)
    )
    return list(rows)


def _allowed(
    booking: Booking,
    server: Server,
    *,
    now: datetime,
    existing: list[Booking],
    quota_hours: int,
    quota_zone: ZoneInfo,
) -> bool:
    """Whether the booking rules that depend on time would accept this candidate."""
    if booking.interval.start > now + BOOKING_HORIZON:
        return False
    if booking.interval.minutes > server.max_booking_hours * booking_rules.MINUTES_PER_HOUR:
        return False
    try:
        booking_rules.check_peak_hours(booking, ZoneInfo(server.lab.timezone))
        booking_rules.check_quota(booking, existing, quota_hours=quota_hours, zone=quota_zone)
    except (PeakHoursLimit, QuotaExceeded):
        return False
    return True


async def search_slots(
    session: AsyncSession,
    viewer: User,
    query: SlotQuery,
    *,
    now: datetime,
    quota_zone: ZoneInfo,
) -> list[SlotCandidate]:
    """Ranked candidates, at most MAX_PER_SERVER per server (not overlapping each
    other) and MAX_RESULTS in all.

    Raises TooManyReservations when the user could not book anything anyway, and
    InvalidSearchWindow for a window that cannot hold the job.
    """
    window = validate(query, now)
    if await reservations.count_active_reservations(session, viewer.id, now) >= (
        reservations.MAX_ACTIVE_RESERVATIONS
    ):
        raise TooManyReservations(
            f"You already hold {reservations.MAX_ACTIVE_RESERVATIONS} upcoming or running "
            "reservations, the most allowed. Cancel one to book another."
        )

    servers = await _candidate_servers(session, query)
    if not servers:
        return []
    busy = await availability_cache.busy_intervals(session, [s.id for s in servers], window)
    weeks = booking_rules.weeks_overlapping(window, quota_zone)
    existing = await reservations.load_quota_bookings(
        session, viewer.id, Interval(weeks[0].start, weeks[-1].end)
    )
    quota_hours = viewer.weekly_gpu_hour_quota

    length = query.duration_minutes // int(SLOT.total_seconds() // 60)
    duration = timedelta(minutes=query.duration_minutes)
    found: list[SlotCandidate] = []
    for server in servers:
        free = booking_rules.free_per_slot(window, server.gpu_count, busy[server.id])
        allowed: list[tuple[int, int]] = []
        for index, fewest in feasible_starts(free, needed=query.gpu_count, length=length):
            start = window.start + index * SLOT
            booking = Booking(Interval(start, start + duration), query.gpu_count)
            if _allowed(
                booking,
                server,
                now=now,
                existing=existing,
                quota_hours=quota_hours,
                quota_zone=quota_zone,
            ):
                allowed.append((index, fewest))
        fewest_by_index = dict(allowed)
        for index in spread_out([i for i, _ in allowed], length=length, limit=MAX_PER_SERVER):
            start = window.start + index * SLOT
            found.append(SlotCandidate(server, start, start + duration, fewest_by_index[index]))

    found.sort(key=lambda c: _rank(c, query.gpu_count))
    return found[:MAX_RESULTS]
