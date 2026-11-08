"""Email bodies for reservation notifications, in plain text and HTML.

Every value that reaches the HTML part is escaped. Purposes and rejection reasons are
typed by users, so they are untrusted; hostnames and names are escaped too, because
there is no reason to trust any field more than another.
"""

import html
from dataclasses import dataclass
from datetime import datetime
from string import Template
from typing import Final
from zoneinfo import ZoneInfo

from app.models import NotificationKind
from app.services.email import OutgoingEmail


@dataclass(frozen=True)
class ReservationFacts:
    """What a notification says about one reservation."""

    recipient_name: str
    recipient_email: str
    hostname: str
    lab_name: str
    timezone: str
    starts_at: datetime
    ends_at: datetime
    gpu_count: int
    purpose: str
    decision_reason: str | None
    # A cancellation of a running reservation ends it early instead.
    ended_early: bool
    link: str


@dataclass(frozen=True)
class _Copy:
    subject: Template
    lead: Template


_COPY: Final[dict[NotificationKind, _Copy]] = {
    NotificationKind.APPROVED: _Copy(
        Template("Approved: $hostname, $when"),
        Template("Your reservation on $hostname has been approved."),
    ),
    NotificationKind.REJECTED: _Copy(
        Template("Not approved: $hostname, $when"),
        Template("Your reservation request on $hostname was not approved."),
    ),
    NotificationKind.CANCELLED: _Copy(
        Template("Cancelled: $hostname, $when"),
        Template("Your reservation on $hostname was cancelled by an administrator."),
    ),
    NotificationKind.EXPIRED: _Copy(
        Template("Expired: $hostname, $when"),
        Template(
            "Your reservation request on $hostname expired because it was not approved "
            "before its start time. Its GPUs have been released."
        ),
    ),
    NotificationKind.REMINDER: _Copy(
        Template("Starting soon: $hostname, $when"),
        Template("Your reservation on $hostname starts within the hour."),
    ),
}

_ENDED_EARLY = Template(
    "Your running reservation on $hostname was ended early by an administrator."
)

_TEXT = Template("""Hello $name,

$lead

Server: $hostname ($lab)
When: $when
GPUs: $gpus
Purpose: $purpose
$reason
See your reservations: $link
""")

_HTML = Template("""<!doctype html>
<html lang="en">
<body style="font-family: system-ui, sans-serif; line-height: 1.5;">
<p>Hello $name,</p>
<p>$lead</p>
<table role="presentation" cellpadding="4">
<tr><th align="left">Server</th><td>$hostname ($lab)</td></tr>
<tr><th align="left">When</th><td>$when</td></tr>
<tr><th align="left">GPUs</th><td>$gpus</td></tr>
<tr><th align="left">Purpose</th><td>$purpose</td></tr>
$reason
</table>
<p><a href="$link">See your reservations</a></p>
</body>
</html>
""")


def format_range(starts_at: datetime, ends_at: datetime, timezone: str) -> str:
    """'Sat 12 Jan 2030, 08:00-09:00 PST' in the lab's zone; the end date is shown
    when it differs from the start date."""
    zone = ZoneInfo(timezone)
    start = starts_at.astimezone(zone)
    end = ends_at.astimezone(zone)
    end_format = "%H:%M %Z" if end.date() == start.date() else "%a %d %b %Y, %H:%M %Z"
    return f"{start:%a %d %b %Y, %H:%M}-{end.strftime(end_format)}"


def render(kind: NotificationKind, facts: ReservationFacts) -> OutgoingEmail:
    copy = _COPY[kind]
    when = format_range(facts.starts_at, facts.ends_at, facts.timezone)
    lead_template = (
        _ENDED_EARLY if kind is NotificationKind.CANCELLED and facts.ended_early else copy.lead
    )
    plain = {
        "name": facts.recipient_name,
        "hostname": facts.hostname,
        "lab": facts.lab_name,
        "when": when,
        "gpus": str(facts.gpu_count),
        "purpose": facts.purpose,
        "link": facts.link,
    }
    lead = lead_template.substitute(hostname=facts.hostname)
    escaped = {key: html.escape(value) for key, value in plain.items()}

    text_reason = f"Reason: {facts.decision_reason}\n" if facts.decision_reason else ""
    html_reason = (
        f'<tr><th align="left">Reason</th><td>{html.escape(facts.decision_reason)}</td></tr>'
        if facts.decision_reason
        else ""
    )
    return OutgoingEmail(
        to_address=facts.recipient_email,
        to_name=facts.recipient_name,
        subject=copy.subject.substitute(hostname=facts.hostname, when=when),
        text=_TEXT.substitute(plain, lead=lead, reason=text_reason),
        html=_HTML.substitute(escaped, lead=html.escape(lead), reason=html_reason),
    )
