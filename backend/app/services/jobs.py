"""Periodic reservation jobs: expiring, completing, reminding, and delivering email.

Every function takes `now` from the caller, so tests can fix the clock, and none of
them commit except where noted: the worker wraps each in a transaction.

Overlapping runs. A slow tick of the sweeping jobs (expire, complete, remind) must not
run next to the following tick. Each takes a transaction-scoped advisory lock named
after the job with pg_try_advisory_xact_lock and returns None at once when another run
holds it. The lock uses the two-key form with JOB_LOCK_NAMESPACE, a key space separate
from the single-key locks on users, so a job can never collide with a user's lock.
The sweeping statements are idempotent anyway; the lock keeps two runs from doing the
same work twice and from blocking each other on the same rows.

Expiry and completion also take `only`, a set of reservation ids, for the demo data
refresh: it applies them to the reservations it just made and to nothing else. Such a
call skips the lock, which is about whole sweeps; the statements stay idempotent.
"""

import logging
import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import ARRAY, BindParameter, DateTime, Uuid, bindparam, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    MAX_DELIVERY_ATTEMPTS,
    GpuAllocation,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reservation,
    ReservationStatus,
)
from app.services import audit, availability_cache, notifications
from app.services.email import EmailSender

logger = logging.getLogger(__name__)

# The first key of the two-key advisory locks the jobs take.
JOB_LOCK_NAMESPACE: Final = 1
REMINDER_LEAD: Final = timedelta(minutes=60)
DISPATCH_BATCH_SIZE: Final = 50
# Long transport errors are cut to this length before being stored.
LAST_ERROR_MAX_LENGTH: Final = 1000
ENTITY_RESERVATION: Final = "reservation"


class Job(StrEnum):
    EXPIRE_PENDING = "expire_pending"
    COMPLETE_FINISHED = "complete_finished"
    QUEUE_REMINDERS = "queue_reminders"
    DISPATCH_NOTIFICATIONS = "dispatch_notifications"
    REFRESH_DEMO = "refresh_demo"


async def try_job_lock(session: AsyncSession, job: Job) -> bool:
    """Take the job's lock until the transaction ends, without waiting. False if
    another run holds it."""
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:namespace, hashtext(:job))"),
        {"namespace": JOB_LOCK_NAMESPACE, "job": job.value},
    )
    return bool(acquired)


def _at(name: str) -> BindParameter[datetime]:
    return bindparam(name, type_=DateTime(timezone=True))


# --- expiring pending reservations ---


async def expire_pending(
    session: AsyncSession, *, now: datetime, only: Collection[uuid.UUID] | None = None
) -> int | None:
    """Mark pending reservations whose start has passed as expired.

    In one transaction, for every reservation with status pending and starts_at <= now:
    - status becomes expired;
    - its allocations become inactive;
    - a system audit row (actor_id NULL, action reservation.expired, payload with
      from_status and to_status) is written;
    - an `expired` notification is queued for the owner, due at `now`.

    Returns how many reservations were expired, or None when another run holds the
    job's lock (see try_job_lock). Running it twice changes nothing the second time.
    A concurrent approval must never leave an approved reservation without active
    allocations, or an expired one with active allocations.
    """
    if only is not None:
        if not only:
            return 0
    elif not await try_job_lock(session, Job.EXPIRE_PENDING):
        return None
    scope = [Reservation.id.in_(only)] if only is not None else []

    # The UPDATE locks each row it changes. A row an approval holds is waited for and
    # then re-checked against the WHERE clause (READ COMMITTED), so a reservation
    # approved in the meantime is no longer pending and is left alone.
    expired = (
        await session.execute(
            update(Reservation)
            .where(
                Reservation.status == ReservationStatus.PENDING,
                Reservation.starts_at <= now,
                *scope,
            )
            .values(status=ReservationStatus.EXPIRED)
            .returning(Reservation.id, Reservation.user_id, Reservation.server_id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    if not expired:
        return 0
    ids = [row.id for row in expired]
    for server_id in {row.server_id for row in expired}:
        availability_cache.mark_changed(session, server_id)

    await session.execute(
        update(GpuAllocation)
        .where(GpuAllocation.reservation_id.in_(ids), GpuAllocation.active.is_(True))
        .values(active=False)
        .execution_options(synchronize_session=False)
    )
    for reservation_id in ids:
        audit.record(
            session,
            actor_id=None,
            action="reservation.expired",
            entity_type=ENTITY_RESERVATION,
            entity_id=reservation_id,
            payload={
                "from_status": ReservationStatus.PENDING.value,
                "to_status": ReservationStatus.EXPIRED.value,
            },
        )
    # ON CONFLICT is a second guard: only rows this statement expired get here, and a
    # reservation expires once, so a conflict would mean a bug elsewhere, not a resend.
    await session.execute(
        insert(Notification)
        .values(
            [
                {
                    "reservation_id": row.id,
                    "recipient_user_id": row.user_id,
                    "kind": NotificationKind.EXPIRED,
                    "next_attempt_at": now,
                }
                for row in expired
            ]
        )
        .on_conflict_do_nothing(constraint="uq_notifications_reservation_kind")
    )
    await session.flush()
    return len(ids)


# --- completing finished reservations ---

_COMPLETE_FINISHED_TEMPLATE = """
WITH finished AS (
    UPDATE reservations
    SET status = 'completed', updated_at = now()
    WHERE status = 'approved' AND ends_at <= :now /* scope */
    RETURNING id, server_id
), released AS (
    UPDATE gpu_allocations
    SET active = false
    WHERE reservation_id IN (SELECT id FROM finished) AND active
    RETURNING 1
), audited AS (
    INSERT INTO audit_log (actor_id, action, entity_type, entity_id, payload)
    SELECT NULL, 'reservation.completed', 'reservation', id,
           '{"from_status": "approved", "to_status": "completed"}'::jsonb
    FROM finished
    RETURNING 1
)
SELECT server_id FROM finished
"""
_COMPLETE_FINISHED = text(_COMPLETE_FINISHED_TEMPLATE).bindparams(_at("now"))
_COMPLETE_FINISHED_ONLY = text(
    _COMPLETE_FINISHED_TEMPLATE.replace("/* scope */", "AND id = ANY(:only)")
).bindparams(_at("now"), bindparam("only", type_=ARRAY(Uuid())))


async def complete_finished(
    session: AsyncSession, *, now: datetime, only: Collection[uuid.UUID] | None = None
) -> int | None:
    """Mark approved reservations whose end has passed as completed, release their
    allocations, and audit each, in one statement.

    Every part of the statement runs against the same snapshot, and the reservations
    it changes are row-locked by the UPDATE. A concurrent early end (cancel) also locks
    the row: whichever commits second re-reads the row and finds it no longer
    approved, so it does nothing (the job) or refuses (the cancel). No email: a
    reservation ending on time is not news.

    Returns how many were completed, or None when another run holds the job's lock.
    """
    if only is not None:
        if not only:
            return 0
        statement, params = _COMPLETE_FINISHED_ONLY, {"now": now, "only": list(only)}
    elif not await try_job_lock(session, Job.COMPLETE_FINISHED):
        return None
    else:
        statement, params = _COMPLETE_FINISHED, {"now": now}
    # One row per completed reservation, naming its server.
    server_ids = list(await session.scalars(statement, params))
    for server_id in set(server_ids):
        availability_cache.mark_changed(session, server_id)
    return len(server_ids)


# --- reminders ---

_QUEUE_REMINDERS = text("""
INSERT INTO notifications (reservation_id, recipient_user_id, kind, next_attempt_at)
SELECT id, user_id, 'reminder', :now
FROM reservations
WHERE status = 'approved' AND starts_at > :now AND starts_at <= :horizon
ON CONFLICT (reservation_id, kind) DO NOTHING
""").bindparams(_at("now"), _at("horizon"))


async def queue_reminders(session: AsyncSession, *, now: datetime) -> int | None:
    """Queue one reminder for every approved reservation starting within the next hour.

    The unique (reservation_id, kind) constraint makes it idempotent: a reservation
    seen by several runs gets one reminder. A booking made less than an hour before
    its start is picked up by the next run, so its reminder can arrive up to one run
    interval after it was booked.

    Returns how many reminders were queued, or None when another run holds the lock.
    """
    if not await try_job_lock(session, Job.QUEUE_REMINDERS):
        return None
    result = await session.execute(_QUEUE_REMINDERS, {"now": now, "horizon": now + REMINDER_LEAD})
    return int(getattr(result, "rowcount", 0) or 0)


# --- delivering notifications ---


@dataclass(frozen=True)
class DispatchReport:
    sent: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0


async def dispatch_notifications(
    factory: async_sessionmaker[AsyncSession],
    sender: EmailSender,
    *,
    now: datetime,
    frontend_url: str,
    suppressed_domains: frozenset[str] = frozenset(),
    batch_size: int = DISPATCH_BATCH_SIZE,
) -> DispatchReport:
    """Deliver due notifications: status pending and next_attempt_at <= now.

    One short transaction per row, from `factory`: claim the oldest due row with
    FOR UPDATE SKIP LOCKED, send it, record the outcome, commit. It stops when nothing
    is due or after `batch_size` rows. A crash between the send and the commit leaves
    the row pending, so at most that one email is sent again.

    For each due row:
    - notifications.prepare_email returns None for a stale reminder: mark it skipped.
    - the recipient's domain is in `suppressed_domains` (addresses that cannot receive
      mail, such as the demo accounts'): mark it skipped without sending.
    - otherwise send it. On success: status sent, sent_at = now.
    - on an exception from the sender: attempts += 1 and last_error = str(error).
      Below MAX_DELIVERY_ATTEMPTS, stay pending with
      next_attempt_at = now + notifications.retry_delay(attempts). At the limit,
      status failed (last_error kept).

    Several dispatchers may run at once (overlapping ticks, more than one worker).
    Dispatchers running at the same time must never both send the same row.

    Returns the counts of what happened to each row it handled.
    """
    report = _Counts()
    for _ in range(batch_size):
        async with factory() as session:
            note = await _claim_next_due(session, now)
            if note is None:
                break
            await _deliver(
                session,
                sender,
                note,
                now=now,
                frontend_url=frontend_url,
                suppressed_domains=suppressed_domains,
                counts=report,
            )
            await session.commit()
    return report.freeze()


@dataclass
class _Counts:
    sent: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0

    def freeze(self) -> DispatchReport:
        return DispatchReport(self.sent, self.skipped, self.retried, self.failed)


async def _claim_next_due(session: AsyncSession, now: datetime) -> Notification | None:
    """The oldest due row, locked until this transaction ends.

    FOR UPDATE is what stops two dispatchers from both sending a row: the second
    cannot read it as pending while the first holds it. SKIP LOCKED makes the second
    move on to the next row instead of waiting behind a slow send.
    """
    return await session.scalar(
        select(Notification)
        .where(
            Notification.status == NotificationStatus.PENDING,
            Notification.next_attempt_at <= now,
        )
        .order_by(Notification.next_attempt_at, Notification.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


async def _deliver(
    session: AsyncSession,
    sender: EmailSender,
    note: Notification,
    *,
    now: datetime,
    frontend_url: str,
    suppressed_domains: frozenset[str],
    counts: _Counts,
) -> None:
    """Send one locked row and record the outcome on it. The caller commits.

    Any failure counts as an attempt, including a bug while preparing the message, so
    a row that always fails is given up on after the last attempt instead of stopping
    every tick at the same place.

    Preparing runs in a savepoint. A database error inside it aborts only the
    savepoint: rolling back to it keeps the transaction usable and keeps the row lock,
    which was taken before the savepoint, so the failure is recorded in the same
    transaction. Without it, the aborted transaction could not record anything, the
    commit would fail, and the row would stay due with its attempts unchanged.
    """
    try:
        async with session.begin_nested():
            email = await notifications.prepare_email(
                session, note, now=now, frontend_url=frontend_url
            )
    except Exception as error:
        _record_failure(note, error, now=now, counts=counts)
        return
    if email is None or _domain(email.to_address) in suppressed_domains:
        note.status = NotificationStatus.SKIPPED
        counts.skipped += 1
        return
    try:
        await sender.send(email)
    except Exception as error:
        _record_failure(note, error, now=now, counts=counts)
        return
    note.status = NotificationStatus.SENT
    note.sent_at = now
    counts.sent += 1


def _domain(address: str) -> str:
    return address.rpartition("@")[2].lower()


def _record_failure(
    note: Notification, error: Exception, *, now: datetime, counts: _Counts
) -> None:
    note.attempts += 1
    note.last_error = (str(error) or type(error).__name__)[:LAST_ERROR_MAX_LENGTH]
    if note.attempts >= MAX_DELIVERY_ATTEMPTS:
        note.status = NotificationStatus.FAILED
        counts.failed += 1
        logger.error(
            "Gave up on notification %s after %d attempts: %s",
            note.id,
            note.attempts,
            note.last_error,
        )
    else:
        note.next_attempt_at = now + notifications.retry_delay(note.attempts)
        counts.retried += 1
        logger.warning(
            "Notification %s failed (attempt %d), retrying at %s: %s",
            note.id,
            note.attempts,
            note.next_attempt_at.isoformat(),
            note.last_error,
        )
