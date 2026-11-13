"""Keeps the demo data current: books the seed plan again around the current time.

The seed places its bookings around the moment it runs, so after about two weeks they
are all in the past. The refresh replaces them, in one transaction:

1. Deletes every reservation flagged `seeded` (allocations and notifications go with
   them by cascade) and marks their servers changed for the availability cache.
2. Books the plan (app.services.demo_plan) again through the same service functions as
   the API, with the clock set to when each step would have happened. Bookings belong
   to the seeded students and the demo admin; decisions are the demo admin's.
3. Expires and completes what is already over among the bookings it just made, and
   only those.
4. Deletes the notification rows those bookings produced, so replaying history emails
   nobody.

What it never touches: users the seed did not create, their reservations, and any
reservation not flagged `seeded`. A plan entry that breaks a rule against current data
(a real booking in its slot, a server in maintenance or removed, a seeded account that
was deactivated) is skipped and reported, never forced: real bookings always win.

Lock order. Every path in the application takes its locks in the order user lock,
then reservation rows, then server row (bookings: user, server; deactivation: user,
reservations; approval: reservation, server). The refresh follows it: it takes the
user lock of every seeded owner first, then the seeded reservation rows (the delete),
and each entry then takes its server row inside create_reservation. Taking the rows
first and the user locks per entry could deadlock with deactivating a seeded student.

Transient failures. Each entry runs in a savepoint. A deadlock or serialization
failure inside one rolls back to that savepoint, keeps every earlier lock and entry,
and is reported as a skip; the next night tries again. Any other database error fails
the whole refresh, which rolls back and leaves yesterday's demo data in place.
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DomainError
from app.models import Lab, Notification, Reservation, Server, User, UserRole
from app.services import audit, demo_plan, jobs, locks
from app.services.availability_cache import mark_changed
from app.services.demo_plan import ADMIN, PLAN, SERVERS, STUDENTS
from app.services.reservations import sqlstate

# Lock conflicts between concurrent transactions: worth a skip and a retry tomorrow,
# not a failed refresh.
TRANSIENT_SQLSTATES: Final = {
    "40P01": "deadlock detected",
    "40001": "serialization failure",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SkippedEntry:
    purpose: str
    reason: str


@dataclass(frozen=True)
class DemoRefresh:
    deleted: int
    created: int
    skipped: tuple[SkippedEntry, ...]
    # Statuses of the bookings it made, after expiry and completion.
    by_status: dict[str, int]


class RefreshInProgress(Exception):
    """Another refresh holds the lock."""


class NothingSeeded(Exception):
    """The database has no seeded students: it was never seeded, so there is no demo
    data to refresh (and no reason to create a demo admin)."""


async def _seeded_students(session: AsyncSession) -> dict[str, User]:
    rows = await session.scalars(select(User).where(User.seeded, User.role == UserRole.STUDENT))
    return {user.email: user for user in rows}


async def _plan_servers(session: AsyncSession) -> dict[str, tuple[uuid.UUID, ZoneInfo]]:
    rows = await session.execute(
        select(Server.hostname, Server.id, Lab.timezone)
        .join(Server.lab)
        .where(Server.hostname.in_([spec.hostname for spec in SERVERS]))
    )
    return {row.hostname: (row.id, ZoneInfo(row.timezone)) for row in rows}


async def _delete_seeded(session: AsyncSession) -> int:
    rows = (
        await session.execute(
            select(Reservation.id, Reservation.server_id)
            .where(Reservation.seeded)
            .with_for_update()
        )
    ).all()
    for server_id in {row.server_id for row in rows}:
        mark_changed(session, server_id)
    if rows:
        await session.execute(
            delete(Reservation).where(Reservation.id.in_([row.id for row in rows]))
        )
    return len(rows)


async def refresh_seeded_reservations(
    session: AsyncSession, *, now: datetime, quota_zone: ZoneInfo
) -> DemoRefresh:
    """Replace the seeded reservations with the plan placed around `now`, in the
    session's transaction. The caller commits (or rolls back, for a dry run).

    Raises RefreshInProgress when another refresh is running, NothingSeeded for a
    database the seed never ran on, and demo_plan.DemoAdminConflict when the demo
    admin's address belongs to someone else.
    """
    if not await jobs.try_job_lock(session, jobs.Job.REFRESH_DEMO):
        raise RefreshInProgress("Another demo data refresh is running.")

    students = await _seeded_students(session)
    if not students:
        raise NothingSeeded("No seeded students: this database has no demo data to refresh.")
    demo_admin = await demo_plan.ensure_demo_admin(session)
    # User locks before the reservation rows; see "Lock order" above. Sorted, so two
    # refreshes would queue in the same order (the job lock already prevents that).
    for owner_id in sorted({user.id for user in students.values()} | {demo_admin.id}):
        await locks.lock_user_bookings(session, owner_id)
    hosts = await _plan_servers(session)
    deleted = await _delete_seeded(session)

    created: list[uuid.UUID] = []
    skipped: list[SkippedEntry] = []
    for booking in PLAN:
        owner = demo_admin if booking.who == ADMIN else students.get(STUDENTS[booking.who][1])
        if owner is None:
            skipped.append(SkippedEntry(booking.purpose, "the seeded student no longer exists"))
            continue
        if booking.hostname not in hosts:
            skipped.append(SkippedEntry(booking.purpose, f"{booking.hostname} no longer exists"))
            continue
        try:
            # A savepoint per entry: a rule broken halfway (booked, then the approval
            # refused) leaves nothing of the entry behind.
            async with session.begin_nested():
                reservation = await demo_plan.book(
                    session,
                    booking,
                    owner=owner,
                    admin=demo_admin,
                    server_id_by_host=hosts,
                    now=now,
                    quota_zone=quota_zone,
                )
        except DomainError as error:
            skipped.append(SkippedEntry(booking.purpose, str(error)))
            continue
        except DBAPIError as error:
            transient = TRANSIENT_SQLSTATES.get(sqlstate(error) or "")
            if transient is None:
                raise
            skipped.append(SkippedEntry(booking.purpose, f"{transient}; retried next run"))
            continue
        created.append(reservation.id)

    await jobs.expire_pending(session, now=now, only=created)
    await jobs.complete_finished(session, now=now, only=created)
    if created:
        await session.execute(delete(Notification).where(Notification.reservation_id.in_(created)))

    statuses = Counter(
        status.value
        for status in await session.scalars(
            select(Reservation.status).where(Reservation.id.in_(created))
        )
    )
    audit.record(
        session,
        actor_id=None,
        action="demo.refresh",
        # The seeded admin stands for the demo data, as the demo account does for resets.
        entity_type="user",
        entity_id=demo_admin.id,
        payload={
            "deleted": deleted,
            "created": len(created),
            "skipped": [{"purpose": s.purpose, "reason": s.reason} for s in skipped],
        },
    )
    await session.flush()
    for entry in skipped:
        logger.warning("demo refresh skipped %r: %s", entry.purpose, entry.reason)
    return DemoRefresh(
        deleted=deleted,
        created=len(created),
        skipped=tuple(skipped),
        by_status=dict(sorted(statuses.items())),
    )
