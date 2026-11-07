"""Reservations: creating them with GPU allocation, quota, and every booking rule.

Concurrency model for creating a reservation, in lock order:
1. A transaction-scoped advisory lock on the user serializes that user's bookings, so
   the active-reservation count and the weekly quota are checked against a stable
   picture of their own reservations. Admin quota changes take the same lock.
2. FOR SHARE on the server row. Changes to a server (gpu_count, status, limits) and
   deletes take FOR UPDATE on the same row, so a booking always sees one consistent
   version of the server and its active GPUs. Bookings on the same server share the
   lock and do not block each other.
3. The exclusion constraint on gpu_allocations decides between bookings that picked the
   same GPU at the same time. The loser rolls back to a savepoint and tries again with
   fresh availability.
No path takes these locks in the opposite order.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager, selectinload

from app.core.exceptions import (
    AllocationContention,
    GpuUnavailable,
    InvalidReservationState,
    InvalidToken,
    ReservationNotFound,
    ServerNotFound,
    ServerUnavailable,
    TooManyReservations,
)
from app.models import (
    CreatedVia,
    Gpu,
    GpuAllocation,
    Reservation,
    ReservationStatus,
    Server,
    ServerStatus,
    User,
    UserRole,
)
from app.models.lab import DEFAULT_LAB_TIMEZONE
from app.models.reservation import HOLDING_STATUSES, QUOTA_STATUSES
from app.services import audit, booking_rules, locks
from app.services.booking_rules import Booking, Interval
from app.services.servers import visibility_conditions

MAX_ACTIVE_RESERVATIONS: Final = 4
MAX_ALLOCATION_ATTEMPTS: Final = 3
# Weekly quotas are counted in one fixed zone, whichever lab a booking is in.
QUOTA_TIMEZONE: Final = ZoneInfo(DEFAULT_LAB_TIMEZONE)

# exclusion_violation: another transaction took one of the chosen GPUs.
EXCLUSION_VIOLATION: Final = "23P01"
# deadlock_detected: not expected, because allocations are inserted in GPU order (see
# _insert_allocations), but retrying is the right response if it ever happens.
DEADLOCK_DETECTED: Final = "40P01"
RETRYABLE_SQLSTATES: Final = frozenset({EXCLUSION_VIOLATION, DEADLOCK_DETECTED})

ENTITY_RESERVATION: Final = "reservation"


def sqlstate(exc: DBAPIError) -> str | None:
    """The Postgres error code of a database error."""
    code = getattr(exc.orig, "sqlstate", None)
    return code if isinstance(code, str) else None


# --- loading ---


async def load_reservation(session: AsyncSession, reservation_id: uuid.UUID) -> Reservation:
    """A reservation with its server, lab, user, and allocated GPUs."""
    reservation = await session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .options(
            selectinload(Reservation.server).selectinload(Server.lab),
            selectinload(Reservation.user),
            selectinload(Reservation.allocations).selectinload(GpuAllocation.gpu),
        )
        .execution_options(populate_existing=True)
    )
    if reservation is None:
        raise ReservationNotFound(f"No reservation with id {reservation_id}.")
    return reservation


# --- locks and reads used by create_reservation ---


async def _lock_user_bookings(session: AsyncSession, user_id: uuid.UUID) -> None:
    await locks.lock_user_bookings(session, user_id)


@dataclass(frozen=True)
class _Account:
    is_active: bool
    quota_hours: int


async def _current_account(session: AsyncSession, user_id: uuid.UUID) -> _Account:
    """Active status and quota as of now, read after the user lock. The caller's User
    object was loaded before the lock, and an admin may have deactivated the account
    or changed the quota since; both changes take the same lock."""
    row = (
        await session.execute(
            select(User.is_active, User.weekly_gpu_hour_quota).where(User.id == user_id)
        )
    ).one()
    return _Account(is_active=row.is_active, quota_hours=row.weekly_gpu_hour_quota)


async def _lock_server_shared(session: AsyncSession, viewer: User, server_id: uuid.UUID) -> Server:
    """The server and its lab, with the server row locked FOR SHARE.

    Servers the viewer cannot see look missing, as on the browse endpoints.
    """
    server = await session.scalar(
        select(Server)
        .join(Server.lab)
        .where(Server.id == server_id, *visibility_conditions(viewer))
        .options(contains_eager(Server.lab))
        .with_for_update(read=True, of=Server)
        .execution_options(populate_existing=True)
    )
    if server is None:
        raise ServerNotFound(f"No server with id {server_id}.")
    return server


async def _count_active_reservations(
    session: AsyncSession, user_id: uuid.UUID, now: datetime
) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(Reservation)
        .where(
            Reservation.user_id == user_id,
            Reservation.status.in_(HOLDING_STATUSES),
            Reservation.ends_at > now,
        )
    )
    return count or 0


async def _load_quota_bookings(
    session: AsyncSession, user_id: uuid.UUID, span: Interval
) -> list[Booking]:
    """The user's reservations that count toward the quota and overlap `span`."""
    rows = await session.execute(
        select(Reservation.starts_at, Reservation.ends_at, Reservation.gpu_count).where(
            Reservation.user_id == user_id,
            Reservation.status.in_(QUOTA_STATUSES),
            Reservation.starts_at < span.end,
            Reservation.ends_at > span.start,
        )
    )
    return [Booking(Interval(row.starts_at, row.ends_at), row.gpu_count) for row in rows]


async def _select_free_gpus(
    session: AsyncSession, server_id: uuid.UUID, interval: Interval
) -> list[Gpu]:
    """Active GPUs of the server with no active allocation overlapping `interval`,
    lowest number first."""
    busy = (
        select(GpuAllocation.id)
        .where(
            GpuAllocation.gpu_id == Gpu.id,
            GpuAllocation.active.is_(True),
            GpuAllocation.starts_at < interval.end,
            GpuAllocation.ends_at > interval.start,
        )
        .correlate(Gpu)
    )
    rows = await session.scalars(
        select(Gpu)
        .where(Gpu.server_id == server_id, Gpu.removed_at.is_(None), ~exists(busy))
        .order_by(Gpu.index)
    )
    return list(rows)


def _insert_allocations(
    session: AsyncSession, reservation: Reservation, gpus: list[Gpu], interval: Interval
) -> None:
    """Add one active allocation per GPU, in ascending GPU number.

    The order matters. An insert that conflicts with an uncommitted allocation waits
    for that transaction. If every transaction inserts in the same order, a transaction
    can only wait for one that is already past that GPU, so waits can never form a
    cycle. The unit of work keeps the order in which objects were added.
    """
    for gpu in sorted(gpus, key=lambda g: g.index):
        session.add(
            GpuAllocation(
                reservation_id=reservation.id,
                gpu_id=gpu.id,
                starts_at=interval.start,
                ends_at=interval.end,
                active=True,
            )
        )


def _gpu_unavailable(hostname: str, free: int, wanted: int) -> GpuUnavailable:
    verb = "is" if free == 1 else "are"
    return GpuUnavailable(
        f"Only {free} of {wanted} requested GPUs {verb} free on {hostname} for that time."
    )


# --- create ---


@dataclass(frozen=True)
class NewReservation:
    server_id: uuid.UUID
    gpu_count: int
    starts_at: datetime
    ends_at: datetime
    purpose: str
    created_via: CreatedVia = CreatedVia.WEB


async def create_reservation(
    session: AsyncSession, actor: User, request: NewReservation, *, now: datetime
) -> Reservation:
    """Book `gpu_count` GPUs on a server for [starts_at, ends_at).

    Raises a DomainError for any rule the request breaks. The caller commits.
    """
    # Only the shape of the request is checked before taking locks. Every rule that
    # reads server or lab data runs after the server lock, so it sees the version of
    # the server that the allocation is made against.
    interval = booking_rules.normalize_range(request.starts_at, request.ends_at)
    booking = Booking(interval, request.gpu_count)

    await _lock_user_bookings(session, actor.id)
    account = await _current_account(session, actor.id)
    if not account.is_active:
        # The same answer the auth layer gives a deactivated account.
        raise InvalidToken("The account is not active.")
    server = await _lock_server_shared(session, actor, request.server_id)
    zone = ZoneInfo(server.lab.timezone)

    booking_rules.check_times(interval, now=now, max_booking_hours=server.max_booking_hours)
    booking_rules.check_gpu_count(
        request.gpu_count, available=server.gpu_count, hostname=server.hostname
    )
    booking_rules.check_peak_hours(booking, zone)
    if server.status is not ServerStatus.AVAILABLE or not server.lab.is_active:
        raise ServerUnavailable(f"{server.hostname} is not open for booking right now.")

    if await _count_active_reservations(session, actor.id, now) >= MAX_ACTIVE_RESERVATIONS:
        raise TooManyReservations(
            f"You can hold at most {MAX_ACTIVE_RESERVATIONS} upcoming or running reservations."
        )
    weeks = booking_rules.weeks_overlapping(interval, QUOTA_TIMEZONE)
    existing = await _load_quota_bookings(
        session, actor.id, Interval(weeks[0].start, weeks[-1].end)
    )
    booking_rules.check_quota(
        booking,
        existing,
        quota_hours=account.quota_hours,
        zone=QUOTA_TIMEZONE,
    )

    status = ReservationStatus.PENDING if server.requires_approval else ReservationStatus.APPROVED
    reservation_id = await _allocate(session, actor, server, request, interval, status=status)
    audit.record(
        session,
        actor_id=actor.id,
        action="reservation.created",
        entity_type=ENTITY_RESERVATION,
        entity_id=reservation_id,
        payload={
            "server_id": str(server.id),
            "gpu_count": request.gpu_count,
            "starts_at": interval.start.isoformat(),
            "ends_at": interval.end.isoformat(),
            "status": status.value,
        },
    )
    await session.flush()
    return await load_reservation(session, reservation_id)


async def _allocate(
    session: AsyncSession,
    actor: User,
    server: Server,
    request: NewReservation,
    interval: Interval,
    *,
    status: ReservationStatus,
) -> uuid.UUID:
    """Insert the reservation and its allocations, retrying when another booking takes
    a chosen GPU first. Returns the new reservation's id."""
    for _attempt in range(MAX_ALLOCATION_ATTEMPTS):
        try:
            async with session.begin_nested():
                free = await _select_free_gpus(session, server.id, interval)
                if len(free) < request.gpu_count:
                    raise _gpu_unavailable(server.hostname, len(free), request.gpu_count)
                # New objects on every attempt: anything added in a rolled-back
                # savepoint is discarded with it.
                reservation = Reservation(
                    user_id=actor.id,
                    server_id=server.id,
                    gpu_count=request.gpu_count,
                    starts_at=interval.start,
                    ends_at=interval.end,
                    status=status,
                    purpose=request.purpose,
                    created_via=request.created_via,
                )
                session.add(reservation)
                await session.flush()
                _insert_allocations(session, reservation, free[: request.gpu_count], interval)
                await session.flush()
                return reservation.id
        except DBAPIError as exc:
            # Rolled back to the savepoint; the user and server locks are still held.
            if sqlstate(exc) not in RETRYABLE_SQLSTATES:
                raise
    raise AllocationContention(
        f"The GPUs on {server.hostname} were taken while booking. Please try again."
    )


# --- ending reservations ---


async def finish_reservation(
    session: AsyncSession,
    reservation: Reservation,
    *,
    now: datetime,
    actor_id: uuid.UUID,
    context: dict[str, str] | None = None,
) -> None:
    """Cancel a reservation, or end it early if it is running. The caller holds the
    reservation's row lock.

    - pending, before or after its start: cancelled.
    - approved, not started (now <= starts_at): cancelled.
    - approved and running (starts_at < now < ends_at): completed, with its end moved
      to the first grid boundary at or after now. New bookings can only start on the
      grid and in the future, so the freed time begins exactly there.
    - anything else: InvalidReservationState.

    Allocations become inactive in the same statement, since only pending and approved
    reservations hold GPUs.
    """
    previous = reservation.status
    original_end = reservation.ends_at
    if previous is ReservationStatus.PENDING or (
        previous is ReservationStatus.APPROVED and now <= reservation.starts_at
    ):
        reservation.status = ReservationStatus.CANCELLED
        action = "reservation.cancelled"
    elif previous is ReservationStatus.APPROVED and reservation.starts_at < now < original_end:
        reservation.status = ReservationStatus.COMPLETED
        reservation.ends_at = min(booking_rules.ceil_to_grid(now), original_end)
        action = "reservation.ended_early"
    else:
        raise InvalidReservationState(
            f"A {previous.value} reservation that has "
            f"{'ended' if now >= original_end else 'not started'} cannot be cancelled."
        )

    await session.execute(
        update(GpuAllocation)
        .where(GpuAllocation.reservation_id == reservation.id)
        .values(active=False, ends_at=reservation.ends_at)
    )
    payload: dict[str, str] = {"from_status": previous.value, "to_status": reservation.status.value}
    if reservation.ends_at != original_end:
        payload |= {
            "original_ends_at": original_end.isoformat(),
            "ends_at": reservation.ends_at.isoformat(),
        }
    audit.record(
        session,
        actor_id=actor_id,
        action=action,
        entity_type=ENTITY_RESERVATION,
        entity_id=reservation.id,
        payload=payload | (context or {}),
    )


async def release_user_reservations(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime, actor_id: uuid.UUID
) -> int:
    """Cancel or end every reservation the user still holds. Used when an account is
    deactivated; the caller holds the user's advisory lock. Returns how many changed."""
    held = await session.scalars(
        select(Reservation)
        .where(
            Reservation.user_id == user_id,
            or_(
                Reservation.status == ReservationStatus.PENDING,
                (Reservation.status == ReservationStatus.APPROVED) & (Reservation.ends_at > now),
            ),
        )
        .order_by(Reservation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    changed = 0
    for reservation in held:
        await finish_reservation(
            session, reservation, now=now, actor_id=actor_id, context={"reason": "user_deactivated"}
        )
        changed += 1
    await session.flush()
    return changed


# --- reading ---


def _can_see(viewer: User, reservation_user_id: uuid.UUID) -> bool:
    """Owners and admins. This does not depend on whether the viewer can still see the
    server: a student keeps their own reservations on a server that has since been
    retired or whose lab was deactivated."""
    return viewer.role is UserRole.ADMIN or viewer.id == reservation_user_id


async def get_reservation(
    session: AsyncSession, viewer: User, reservation_id: uuid.UUID
) -> Reservation:
    reservation = await load_reservation(session, reservation_id)
    if not _can_see(viewer, reservation.user_id):
        raise ReservationNotFound(f"No reservation with id {reservation_id}.")
    return reservation


@dataclass(frozen=True)
class ReservationFilters:
    status: ReservationStatus | None = None
    server_id: uuid.UUID | None = None
    # Admins only; a student's list is always their own.
    user_id: uuid.UUID | None = None
    # Reservations overlapping [overlaps_from, overlaps_to).
    overlaps_from: datetime | None = None
    overlaps_to: datetime | None = None


@dataclass(frozen=True)
class ReservationListing:
    items: list[Reservation]
    total: int


async def list_reservations(
    session: AsyncSession,
    viewer: User,
    filters: ReservationFilters,
    *,
    offset: int,
    limit: int,
) -> ReservationListing:
    """Newest start first. Related rows are loaded with one query per relationship,
    so a page costs the same number of statements whatever its size."""
    conditions = []
    if viewer.role is not UserRole.ADMIN:
        conditions.append(Reservation.user_id == viewer.id)
    elif filters.user_id is not None:
        conditions.append(Reservation.user_id == filters.user_id)
    if filters.status is not None:
        conditions.append(Reservation.status == filters.status)
    if filters.server_id is not None:
        conditions.append(Reservation.server_id == filters.server_id)
    if filters.overlaps_to is not None:
        conditions.append(Reservation.starts_at < filters.overlaps_to)
    if filters.overlaps_from is not None:
        conditions.append(Reservation.ends_at > filters.overlaps_from)

    total = await session.scalar(select(func.count()).select_from(Reservation).where(*conditions))
    rows = await session.scalars(
        select(Reservation)
        .where(*conditions)
        .options(
            selectinload(Reservation.server).selectinload(Server.lab),
            selectinload(Reservation.user),
            selectinload(Reservation.allocations).selectinload(GpuAllocation.gpu),
        )
        .order_by(Reservation.starts_at.desc(), Reservation.id)
        .offset(offset)
        .limit(limit)
    )
    return ReservationListing(items=list(rows), total=total or 0)


# --- transitions ---


async def _lock_reservation(
    session: AsyncSession, viewer: User, reservation_id: uuid.UUID
) -> Reservation:
    """The reservation with its row locked FOR UPDATE until the transaction ends.

    Every transition takes this lock and nothing else, so two transitions on one
    reservation run one after the other and the second sees the first's result. Paths
    that also need the user's advisory lock (deactivation) take it before this one.
    """
    reservation = await session.scalar(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if reservation is None or not _can_see(viewer, reservation.user_id):
        raise ReservationNotFound(f"No reservation with id {reservation_id}.")
    return reservation


async def cancel_reservation(
    session: AsyncSession, actor: User, reservation_id: uuid.UUID, *, now: datetime
) -> Reservation:
    """Cancel, or end early if running. Owners and admins; see finish_reservation."""
    reservation = await _lock_reservation(session, actor, reservation_id)
    await finish_reservation(session, reservation, now=now, actor_id=actor.id)
    await session.flush()
    return await load_reservation(session, reservation_id)


async def approve_reservation(
    session: AsyncSession, admin: User, reservation_id: uuid.UUID, *, now: datetime
) -> Reservation:
    """Approve a pending reservation that has not started. Its GPUs are already held."""
    reservation = await _lock_reservation(session, admin, reservation_id)
    if reservation.status is not ReservationStatus.PENDING:
        raise InvalidReservationState(
            f"Only pending reservations can be approved; this one is {reservation.status.value}."
        )
    if reservation.starts_at <= now:
        raise InvalidReservationState("This reservation has already started.")
    reservation.status = ReservationStatus.APPROVED
    reservation.decided_by = admin.id
    reservation.decided_at = now
    _record_decision(session, admin, reservation, "reservation.approved")
    await session.flush()
    return await load_reservation(session, reservation_id)


async def reject_reservation(
    session: AsyncSession,
    admin: User,
    reservation_id: uuid.UUID,
    *,
    reason: str,
    now: datetime,
) -> Reservation:
    """Reject a pending reservation and release its GPUs."""
    reservation = await _lock_reservation(session, admin, reservation_id)
    if reservation.status is not ReservationStatus.PENDING:
        raise InvalidReservationState(
            f"Only pending reservations can be rejected; this one is {reservation.status.value}."
        )
    reservation.status = ReservationStatus.REJECTED
    reservation.decided_by = admin.id
    reservation.decided_at = now
    reservation.decision_reason = reason
    await session.execute(
        update(GpuAllocation)
        .where(GpuAllocation.reservation_id == reservation.id)
        .values(active=False)
    )
    _record_decision(session, admin, reservation, "reservation.rejected", reason=reason)
    await session.flush()
    return await load_reservation(session, reservation_id)


def _record_decision(
    session: AsyncSession,
    admin: User,
    reservation: Reservation,
    action: str,
    *,
    reason: str | None = None,
) -> None:
    payload = {
        "from_status": ReservationStatus.PENDING.value,
        "to_status": reservation.status.value,
    }
    if reason is not None:
        payload["reason"] = reason
    audit.record(
        session,
        actor_id=admin.id,
        action=action,
        entity_type=ENTITY_RESERVATION,
        entity_id=reservation.id,
        payload=payload,
    )
