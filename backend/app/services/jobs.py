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
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import BindParameter, DateTime, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.email import EmailSender

# The first key of the two-key advisory locks the jobs take.
JOB_LOCK_NAMESPACE: Final = 1
REMINDER_LEAD: Final = timedelta(minutes=60)
DISPATCH_BATCH_SIZE: Final = 50


class Job(StrEnum):
    EXPIRE_PENDING = "expire_pending"
    COMPLETE_FINISHED = "complete_finished"
    QUEUE_REMINDERS = "queue_reminders"
    DISPATCH_NOTIFICATIONS = "dispatch_notifications"


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


async def expire_pending(session: AsyncSession, *, now: datetime) -> int | None:
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
    raise NotImplementedError("expire_pending is not implemented yet")


# --- completing finished reservations ---

_COMPLETE_FINISHED = text("""
WITH finished AS (
    UPDATE reservations
    SET status = 'completed', updated_at = now()
    WHERE status = 'approved' AND ends_at <= :now
    RETURNING id
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
SELECT count(*) FROM finished
""").bindparams(_at("now"))


async def complete_finished(session: AsyncSession, *, now: datetime) -> int | None:
    """Mark approved reservations whose end has passed as completed, release their
    allocations, and audit each, in one statement.

    Every part of the statement runs against the same snapshot, and the reservations
    it changes are row-locked by the UPDATE. A concurrent early end (cancel) also locks
    the row: whichever commits second re-reads the row and finds it no longer
    approved, so it does nothing (the job) or refuses (the cancel). No email: a
    reservation ending on time is not news.

    Returns how many were completed, or None when another run holds the job's lock.
    """
    if not await try_job_lock(session, Job.COMPLETE_FINISHED):
        return None
    count = await session.scalar(_COMPLETE_FINISHED, {"now": now})
    return count or 0


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
    batch_size: int = DISPATCH_BATCH_SIZE,
) -> DispatchReport:
    """Deliver due notifications: status pending and next_attempt_at <= now.

    Opens its own sessions from `factory` and commits them itself, because whether
    the send happens inside or between transactions is part of the design.

    For each due row:
    - notifications.prepare_email returns None for a stale reminder: mark it skipped.
    - otherwise send it. On success: status sent, sent_at = now.
    - on an exception from the sender: attempts += 1 and last_error = str(error).
      Below MAX_DELIVERY_ATTEMPTS, stay pending with
      next_attempt_at = now + notifications.retry_delay(attempts). At the limit,
      status failed (last_error kept).

    Several dispatchers may run at once (overlapping ticks, more than one worker).
    Dispatchers running at the same time must never both send the same row.

    Returns the counts of what happened to each row it handled.
    """
    raise NotImplementedError("dispatch_notifications is not implemented yet")
