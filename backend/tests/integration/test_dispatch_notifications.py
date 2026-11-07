"""dispatch_notifications: delivering the outbox.

These tests describe the job before it exists. They fix the behavior every design
must have: what is due, staleness, retries with backoff, giving up, batching, and
that dispatchers running at the same time never send a row twice. Tests that depend
on the chosen concurrency design (a negative control for it, crash recovery) are
added once it is chosen.
"""

import asyncio
import smtplib
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    MAX_DELIVERY_ATTEMPTS,
    Notification,
    NotificationKind,
    NotificationStatus,
    Reservation,
    User,
    UserRole,
)
from app.services import jobs, reservations
from app.services.email import OutgoingEmail
from tests.integration.reservation_helpers import add_lab, add_server, add_user, book

pytestmark = pytest.mark.integration

# Monday 7 January 2030, 10:00 in Los Angeles; bookings are that Saturday morning.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
FRONTEND: Final = "http://app.test"
# Kinds that never go stale, so rows of these kinds are always sent.
REPORT_KINDS: Final = (
    NotificationKind.APPROVED,
    NotificationKind.REJECTED,
    NotificationKind.CANCELLED,
    NotificationKind.EXPIRED,
)


class FakeSender:
    """Records what it sends. Fails for recipients in `failing`, and can take a while
    per message so that concurrent dispatchers overlap."""

    def __init__(self, *, failing: Sequence[str] = (), delay: float = 0.0) -> None:
        self.sent: list[OutgoingEmail] = []
        self.failing = set(failing)
        self.delay = delay

    async def send(self, email: OutgoingEmail) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if email.to_address in self.failing:
            raise smtplib.SMTPResponseException(451, b"4.3.0 Mailbox temporarily unavailable")
        self.sent.append(email)


async def _rows(
    factory: async_sessionmaker[AsyncSession], kinds: Sequence[NotificationKind], *, users: int = 1
) -> list[uuid.UUID]:
    """One reservation per user, and one due notification of each kind per
    reservation. Returns the notification ids."""
    async with factory() as db:
        server = await add_server(db, await add_lab(db), gpu_count=8)
        ids: list[uuid.UUID] = []
        for i in range(users):
            user = await add_user(db, f"student{i:02}@example.edu")
            reservation = await book(db, user, server, SATURDAY, now=NOW)
            for kind in kinds:
                note = Notification(
                    reservation_id=reservation.id,
                    recipient_user_id=user.id,
                    kind=kind,
                    next_attempt_at=NOW,
                )
                db.add(note)
                await db.flush()
                ids.append(note.id)
        await db.commit()
    return ids


async def _get(factory: async_sessionmaker[AsyncSession], note_id: uuid.UUID) -> Notification:
    async with factory() as db:
        note = await db.get(Notification, note_id)
        assert note is not None
        return note


async def _dispatch(
    factory: async_sessionmaker[AsyncSession],
    sender: FakeSender,
    now: datetime = NOW,
    batch_size: int = jobs.DISPATCH_BATCH_SIZE,
) -> jobs.DispatchReport:
    return await jobs.dispatch_notifications(
        factory, sender, now=now, frontend_url=FRONTEND, batch_size=batch_size
    )


# --- sending ---


async def test_sends_due_notifications_and_marks_them_sent(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.APPROVED])
    sender = FakeSender()

    report = await _dispatch(job_factory, sender)

    assert report == jobs.DispatchReport(sent=1)
    [email] = sender.sent
    assert email.to_address == "student00@example.edu"
    assert email.subject.startswith("Approved: a100-node-01,")
    assert f"{FRONTEND}/reservations" in email.text
    note = await _get(job_factory, note_id)
    assert note.status is NotificationStatus.SENT
    assert note.sent_at == NOW


async def test_rejection_emails_carry_the_reason(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with job_factory() as db:
        lab = await add_lab(db)
        server = await add_server(db, lab, "h100-node-01", requires_approval=True)
        student = await add_user(db, "student@example.edu")
        admin = await add_user(db, "admin@example.edu", role=UserRole.ADMIN)
        pending = await book(db, student, server, SATURDAY, now=NOW)
        await reservations.reject_reservation(
            db, admin, pending.id, reason="Reserved for the <course> deadline", now=NOW
        )
        await db.commit()
    sender = FakeSender()

    await _dispatch(job_factory, sender)

    [email] = sender.sent
    assert "Reason: Reserved for the <course> deadline" in email.text
    assert "Reserved for the &lt;course&gt; deadline" in email.html


async def test_rows_not_yet_due_wait(job_factory: async_sessionmaker[AsyncSession]) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.APPROVED])
    async with job_factory() as db:
        await db.execute(
            update(Notification)
            .where(Notification.id == note_id)
            .values(next_attempt_at=NOW + timedelta(minutes=5))
        )
        await db.commit()
    sender = FakeSender()

    assert await _dispatch(job_factory, sender) == jobs.DispatchReport()
    assert sender.sent == []
    assert (await _get(job_factory, note_id)).status is NotificationStatus.PENDING


async def test_sent_rows_are_not_sent_again(job_factory: async_sessionmaker[AsyncSession]) -> None:
    await _rows(job_factory, [NotificationKind.APPROVED])
    sender = FakeSender()

    await _dispatch(job_factory, sender)
    second = await _dispatch(job_factory, sender, now=NOW + timedelta(hours=1))

    assert second == jobs.DispatchReport()
    assert len(sender.sent) == 1


async def test_batch_size_limits_one_run(job_factory: async_sessionmaker[AsyncSession]) -> None:
    await _rows(job_factory, REPORT_KINDS[:3])
    sender = FakeSender()

    first = await _dispatch(job_factory, sender, batch_size=2)
    second = await _dispatch(job_factory, sender, batch_size=2)

    assert (first.sent, second.sent) == (2, 1)
    assert len(sender.sent) == 3


# --- staleness ---


async def test_a_reminder_for_a_cancelled_reservation_is_skipped(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.REMINDER])
    async with job_factory() as db:
        await db.execute(update(Reservation).values(status="cancelled"))
        await db.commit()
    sender = FakeSender()

    report = await _dispatch(job_factory, sender, now=SATURDAY - timedelta(minutes=30))

    assert report == jobs.DispatchReport(skipped=1)
    assert sender.sent == []
    note = await _get(job_factory, note_id)
    assert note.status is NotificationStatus.SKIPPED
    assert note.sent_at is None


async def test_a_reminder_that_comes_due_after_the_start_is_skipped(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A reminder held back by retries past the start is no longer useful."""
    [note_id] = await _rows(job_factory, [NotificationKind.REMINDER])
    sender = FakeSender()

    report = await _dispatch(job_factory, sender, now=SATURDAY + timedelta(minutes=1))

    assert report == jobs.DispatchReport(skipped=1)
    assert (await _get(job_factory, note_id)).status is NotificationStatus.SKIPPED


async def test_a_timely_reminder_is_sent(job_factory: async_sessionmaker[AsyncSession]) -> None:
    await _rows(job_factory, [NotificationKind.REMINDER])
    sender = FakeSender()

    report = await _dispatch(job_factory, sender, now=SATURDAY - timedelta(minutes=30))

    assert report == jobs.DispatchReport(sent=1)
    assert sender.sent[0].subject.startswith("Starting soon:")


# --- failures ---


async def test_a_failure_is_retried_later_with_the_error_kept(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.APPROVED])
    failing = FakeSender(failing=["student00@example.edu"])

    report = await _dispatch(job_factory, failing)

    assert report == jobs.DispatchReport(retried=1)
    note = await _get(job_factory, note_id)
    assert note.status is NotificationStatus.PENDING
    assert note.attempts == 1
    assert note.next_attempt_at == NOW + timedelta(minutes=1)
    assert note.last_error is not None and "451" in note.last_error

    # Not due again until the delay has passed; then a working transport delivers it.
    working = FakeSender()
    assert await _dispatch(job_factory, working, now=NOW + timedelta(seconds=30)) == (
        jobs.DispatchReport()
    )
    assert await _dispatch(job_factory, working, now=NOW + timedelta(minutes=1)) == (
        jobs.DispatchReport(sent=1)
    )
    assert (await _get(job_factory, note_id)).status is NotificationStatus.SENT


async def test_backoff_grows_with_each_failure(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.APPROVED])
    failing = FakeSender(failing=["student00@example.edu"])
    now = NOW
    delays = []
    for _ in range(3):
        await _dispatch(job_factory, failing, now=now)
        note = await _get(job_factory, note_id)
        delays.append(note.next_attempt_at - now)
        now = note.next_attempt_at

    assert delays == [timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=4)]


async def test_gives_up_after_the_last_attempt(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    [note_id] = await _rows(job_factory, [NotificationKind.APPROVED])
    async with job_factory() as db:
        await db.execute(
            update(Notification)
            .where(Notification.id == note_id)
            .values(attempts=MAX_DELIVERY_ATTEMPTS - 1, last_error="earlier failure")
        )
        await db.commit()
    failing = FakeSender(failing=["student00@example.edu"])

    report = await _dispatch(job_factory, failing)

    assert report == jobs.DispatchReport(failed=1)
    note = await _get(job_factory, note_id)
    assert note.status is NotificationStatus.FAILED
    assert note.attempts == MAX_DELIVERY_ATTEMPTS
    assert note.last_error is not None and "451" in note.last_error
    assert await _dispatch(job_factory, FakeSender(), now=NOW + timedelta(days=1)) == (
        jobs.DispatchReport()
    )


async def test_one_failure_does_not_hold_up_the_others(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _rows(job_factory, [NotificationKind.APPROVED], users=2)
    sender = FakeSender(failing=["student00@example.edu"])

    report = await _dispatch(job_factory, sender)

    assert report == jobs.DispatchReport(sent=1, retried=1)
    assert [e.to_address for e in sender.sent] == ["student01@example.edu"]


# --- concurrent dispatchers ---


async def test_concurrent_dispatchers_never_send_a_row_twice(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two dispatchers start together on twelve due rows, each slow enough per message
    that they overlap. Every row is sent exactly once in total."""
    ids = await _rows(job_factory, REPORT_KINDS, users=3)
    sender = FakeSender(delay=0.05)

    reports = await asyncio.gather(
        _dispatch(job_factory, sender, batch_size=len(ids)),
        _dispatch(job_factory, sender, batch_size=len(ids)),
    )

    assert sum(r.sent for r in reports) == len(ids)
    sent_pairs = [(e.to_address, e.subject.split(":")[0]) for e in sender.sent]
    assert len(sent_pairs) == len(set(sent_pairs)) == len(ids)
    async with job_factory() as db:
        statuses = list(await db.scalars(select(Notification.status)))
    assert statuses == [NotificationStatus.SENT] * len(ids)


async def test_inactive_recipients_still_receive_their_notification(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A deactivated user is told their bookings were released."""
    await _rows(job_factory, [NotificationKind.CANCELLED])
    async with job_factory() as db:
        await db.execute(update(User).values(is_active=False))
        await db.commit()
    sender = FakeSender()

    assert await _dispatch(job_factory, sender) == jobs.DispatchReport(sent=1)
