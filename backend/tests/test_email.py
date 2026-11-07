"""Email rendering and transports, without a mail server."""

import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from app.core.config import EmailBackend, Settings
from app.models import (
    Notification,
    NotificationKind,
    Reservation,
    ReservationStatus,
)
from app.services import email, notifications
from app.services.email import OutgoingEmail, SesSender, SmtpSender, build_sender
from app.services.email_templates import ReservationFacts, format_range, render

START = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)  # Saturday 08:00 in Los Angeles


def facts(**overrides: Any) -> ReservationFacts:
    values: dict[str, Any] = {
        "recipient_name": "Ada Lovelace",
        "recipient_email": "ada@example.edu",
        "hostname": "a100-node-01",
        "lab_name": "Vision Lab",
        "timezone": "America/Los_Angeles",
        "starts_at": START,
        "ends_at": START + timedelta(hours=2),
        "gpu_count": 2,
        "purpose": "Fine-tuning a 7B model",
        "decision_reason": None,
        "ended_early": False,
        "link": "http://localhost:5173/reservations",
    }
    values.update(overrides)
    return ReservationFacts(**values)


# --- templates ---


def test_times_are_shown_in_the_lab_zone() -> None:
    assert format_range(START, START + timedelta(hours=2), "America/Los_Angeles") == (
        "Sat 12 Jan 2030, 08:00-10:00 PST"
    )


def test_overnight_ranges_show_the_end_date() -> None:
    late = datetime(2030, 1, 12, 6, 0, tzinfo=UTC)  # Friday 22:00 PST
    assert format_range(late, late + timedelta(hours=4), "America/Los_Angeles") == (
        "Fri 11 Jan 2030, 22:00-Sat 12 Jan 2030, 02:00 PST"
    )


@pytest.mark.parametrize(
    ("kind", "subject"),
    [
        (NotificationKind.APPROVED, "Approved: a100-node-01, Sat 12 Jan 2030, 08:00-10:00 PST"),
        (NotificationKind.REJECTED, "Not approved: a100-node-01, Sat 12 Jan 2030, 08:00-10:00 PST"),
        (NotificationKind.CANCELLED, "Cancelled: a100-node-01, Sat 12 Jan 2030, 08:00-10:00 PST"),
        (NotificationKind.EXPIRED, "Expired: a100-node-01, Sat 12 Jan 2030, 08:00-10:00 PST"),
        (
            NotificationKind.REMINDER,
            "Starting soon: a100-node-01, Sat 12 Jan 2030, 08:00-10:00 PST",
        ),
    ],
)
def test_every_kind_has_a_subject(kind: NotificationKind, subject: str) -> None:
    rendered = render(kind, facts())

    assert rendered.subject == subject
    assert rendered.to_address == "ada@example.edu"
    assert "Hello Ada Lovelace," in rendered.text
    assert "http://localhost:5173/reservations" in rendered.html


def test_html_escapes_user_supplied_text() -> None:
    script = "<script>alert('x')</script>"
    rendered = render(
        NotificationKind.REJECTED,
        facts(purpose=f"Train {script}", decision_reason=f"No {script} & no <b>bold</b>"),
    )

    assert "<script>" not in rendered.html
    assert "<b>" not in rendered.html
    assert "Train &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in rendered.html
    assert (
        "No &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt; &amp; no &lt;b&gt;bold"
        in rendered.html
    )
    # The plain-text part is not HTML, so it keeps the text as typed.
    assert f"Purpose: Train {script}" in rendered.text
    assert f"Reason: No {script}" in rendered.text


def test_names_and_hostnames_are_escaped_too() -> None:
    rendered = render(
        NotificationKind.APPROVED, facts(recipient_name="<i>Ada</i>", lab_name="Lab & Co")
    )

    assert "Hello &lt;i&gt;Ada&lt;/i&gt;," in rendered.html
    assert "Lab &amp; Co" in rendered.html


def test_dollar_signs_in_user_text_are_left_alone() -> None:
    rendered = render(NotificationKind.APPROVED, facts(purpose="Budget $hostname $$"))

    assert "Budget $hostname $$" in rendered.text


def test_reason_appears_only_when_given() -> None:
    assert "Reason" not in render(NotificationKind.APPROVED, facts()).html


def test_ending_early_has_its_own_wording() -> None:
    rendered = render(NotificationKind.CANCELLED, facts(ended_early=True))

    assert "was ended early by an administrator" in rendered.text


# --- transports ---


def outgoing() -> OutgoingEmail:
    return render(NotificationKind.APPROVED, facts())


class FakeSmtp:
    sent: ClassVar[list[EmailMessage]] = []
    opened: ClassVar[list[tuple[str, int, float]]] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        FakeSmtp.opened.append((host, port, timeout))

    def __enter__(self) -> "FakeSmtp":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def send_message(self, message: EmailMessage) -> None:
        FakeSmtp.sent.append(message)


async def test_smtp_sends_a_multipart_message(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSmtp.sent, FakeSmtp.opened = [], []
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSmtp)

    await SmtpSender("mailpit", 1025, "GPU Reserve <no-reply@example.test>", 5.0).send(outgoing())

    assert FakeSmtp.opened == [("mailpit", 1025, 5.0)]
    [message] = FakeSmtp.sent
    assert message["To"] == "Ada Lovelace <ada@example.edu>"
    assert message["From"] == "GPU Reserve <no-reply@example.test>"
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]


async def test_ses_sends_the_raw_message() -> None:
    calls: list[dict[str, Any]] = []

    class FakeSes:
        def send_email(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    await SesSender("no-reply@example.test", None, client=FakeSes()).send(outgoing())

    [call] = calls
    assert call["Destination"] == {"ToAddresses": ["ada@example.edu"]}
    assert b"Subject: Approved: a100-node-01" in call["Content"]["Raw"]["Data"]


def test_the_backend_setting_picks_the_sender() -> None:
    assert isinstance(build_sender(Settings()), SmtpSender)
    ses = build_sender(Settings(email_backend=EmailBackend.SES, ses_region="us-west-2"))
    assert isinstance(ses, SesSender)


# --- retry and staleness ---


@pytest.mark.parametrize(
    ("attempts", "minutes"), [(1, 1), (2, 2), (3, 4), (4, 8), (7, 60), (20, 60)]
)
def test_retry_delay_doubles_up_to_an_hour(attempts: int, minutes: int) -> None:
    assert notifications.retry_delay(attempts) == timedelta(minutes=minutes)


def test_retry_delay_needs_a_failure() -> None:
    with pytest.raises(ValueError):
        notifications.retry_delay(0)


def _reservation(status: ReservationStatus) -> Reservation:
    return Reservation(
        id=uuid.uuid4(), status=status, starts_at=START, ends_at=START + timedelta(hours=1)
    )


def _notification(kind: NotificationKind) -> Notification:
    return Notification(kind=kind)


@pytest.mark.parametrize(
    ("status", "now", "stale"),
    [
        (ReservationStatus.APPROVED, START - timedelta(minutes=30), False),
        (ReservationStatus.APPROVED, START, True),
        (ReservationStatus.CANCELLED, START - timedelta(minutes=30), True),
        (ReservationStatus.COMPLETED, START - timedelta(minutes=30), True),
    ],
)
def test_reminders_go_stale(status: ReservationStatus, now: datetime, stale: bool) -> None:
    note = _notification(NotificationKind.REMINDER)
    assert notifications.is_stale(note, _reservation(status), now=now) is stale


@pytest.mark.parametrize(
    "kind",
    [
        NotificationKind.APPROVED,
        NotificationKind.REJECTED,
        NotificationKind.CANCELLED,
        NotificationKind.EXPIRED,
    ],
)
def test_reports_of_a_change_never_go_stale(kind: NotificationKind) -> None:
    """An approval followed by a cancellation still sends both: each reports a change
    that happened."""
    later = START + timedelta(days=1)
    reservation = _reservation(ReservationStatus.CANCELLED)

    assert notifications.is_stale(_notification(kind), reservation, now=later) is False
