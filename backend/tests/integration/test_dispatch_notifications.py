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
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.selectable import GenerativeSelect

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
from tests.integration.concurrency import BARRIER_TIMEOUT, LOCK_WAIT_TIMEOUT, POLL_INTERVAL, Gate
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


async def test_batch_size_caps_one_tick_and_the_next_tick_continues(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
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


# --- what SKIP LOCKED and the row lock each contribute ---
#
# The controls below patch SQLAlchemy's with_for_update for the duration of the test,
# so they apply to however dispatch builds its SELECT ... FOR UPDATE SKIP LOCKED.
# They assume the query is built with select(...).with_for_update(...), not text().


class HoldingSender(FakeSender):
    """Holds the first message it is given until `release` is set, as a slow mail
    server would; every later message goes through at once."""

    def __init__(self) -> None:
        super().__init__()
        self.holding = asyncio.Event()
        self.release = asyncio.Event()
        self._held = False

    async def send(self, email: OutgoingEmail) -> None:
        if not self._held:
            self._held = True
            self.holding.set()
            await asyncio.wait_for(self.release.wait(), LOCK_WAIT_TIMEOUT)
        await super().send(email)


async def _lock_waiters(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
        )
    return count or 0


async def _someone_waits_on_a_lock(engine: AsyncEngine) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + LOCK_WAIT_TIMEOUT
    while loop.time() < deadline:
        if await _lock_waiters(engine):
            return True
        await asyncio.sleep(POLL_INTERVAL)
    return False


def _patch_for_update(monkeypatch: pytest.MonkeyPatch, **forced: bool) -> None:
    original = GenerativeSelect.with_for_update

    def patched(self: GenerativeSelect, **kwargs: object) -> GenerativeSelect:
        return original(self, **(kwargs | forced))  # type: ignore[arg-type]

    monkeypatch.setattr(GenerativeSelect, "with_for_update", patched)


def _drop_row_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_lock(self: GenerativeSelect, **_: object) -> GenerativeSelect:
        return self

    monkeypatch.setattr(GenerativeSelect, "with_for_update", no_lock)


async def test_a_stuck_send_does_not_hold_up_another_dispatcher(
    job_factory: async_sessionmaker[AsyncSession],
) -> None:
    """With SKIP LOCKED, while one dispatcher is stuck sending (holding its row lock),
    another skips that row and delivers the next one."""
    await _rows(job_factory, [NotificationKind.APPROVED], users=2)
    sender = HoldingSender()
    stuck = asyncio.create_task(_dispatch(job_factory, sender))
    try:
        await asyncio.wait_for(sender.holding.wait(), LOCK_WAIT_TIMEOUT)

        other = await asyncio.wait_for(_dispatch(job_factory, sender), LOCK_WAIT_TIMEOUT)

        assert other == jobs.DispatchReport(sent=1)
        assert len(sender.sent) == 1  # delivered while the first send is still stuck
    finally:
        sender.release.set()
        await asyncio.wait({stuck}, timeout=LOCK_WAIT_TIMEOUT)
    assert stuck.result() == jobs.DispatchReport(sent=1)
    assert len({e.to_address for e in sender.sent}) == len(sender.sent) == 2


async def test_control_without_skip_locked_the_other_dispatcher_waits(
    job_factory: async_sessionmaker[AsyncSession],
    job_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control for SKIP LOCKED. Plain FOR UPDATE still never sends a row
    twice: the waiting dispatcher re-checks the row after the lock is released and
    finds it sent. What it loses is progress: the second dispatcher waits behind the
    stuck send and delivers nothing meanwhile."""
    await _rows(job_factory, [NotificationKind.APPROVED], users=2)
    _patch_for_update(monkeypatch, skip_locked=False)
    sender = HoldingSender()
    stuck = asyncio.create_task(_dispatch(job_factory, sender))
    other: asyncio.Task[jobs.DispatchReport] | None = None
    try:
        await asyncio.wait_for(sender.holding.wait(), LOCK_WAIT_TIMEOUT)
        other = asyncio.create_task(_dispatch(job_factory, sender))

        assert await _someone_waits_on_a_lock(job_engine)
        assert sender.sent == []
    finally:
        sender.release.set()
        pending = {t for t in (stuck, other) if t is not None}
        await asyncio.wait(pending, timeout=LOCK_WAIT_TIMEOUT)
    assert other is not None
    # Serialized, not duplicated.
    assert len({e.to_address for e in sender.sent}) == len(sender.sent) == 2


async def test_control_without_the_row_lock_a_row_is_sent_twice(
    job_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control for FOR UPDATE itself. Without the lock, two dispatchers both
    read the same due row; the sender holds both at a barrier so both have read it
    before either marks it sent. The one email goes out twice."""
    await _rows(job_factory, [NotificationKind.APPROVED])
    _drop_row_lock(monkeypatch)
    gate = Gate(2)

    class MeetingSender(FakeSender):
        async def send(self, email: OutgoingEmail) -> None:
            await gate.wait()
            await super().send(email)

    sender = MeetingSender()

    await asyncio.wait_for(
        asyncio.gather(_dispatch(job_factory, sender), _dispatch(job_factory, sender)),
        BARRIER_TIMEOUT,
    )

    assert [e.to_address for e in sender.sent] == ["student00@example.edu"] * 2
