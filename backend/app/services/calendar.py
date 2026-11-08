"""An iCalendar (RFC 5545) file for one reservation, written by hand.

The format needed here is small: one VEVENT with UTC times. A library would add a
dependency for a dozen lines; the rules that are easy to get wrong (escaping, CRLF,
folding at 75 octets without splitting a UTF-8 character) are tested directly.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ReservationNotFound
from app.models import AuditLog, ReservationStatus, User
from app.services.reservations import load_reservation

PRODID: Final = "-//GPU Reserve//Reservations//EN"
UID_DOMAIN: Final = "gpu-reserve"
MAX_LINE_OCTETS: Final = 75
CRLF: Final = "\r\n"
ENTITY_RESERVATION: Final = "reservation"

_ICAL_STATUS: Final = {
    ReservationStatus.PENDING: "TENTATIVE",
    ReservationStatus.APPROVED: "CONFIRMED",
    ReservationStatus.COMPLETED: "CONFIRMED",
    ReservationStatus.REJECTED: "CANCELLED",
    ReservationStatus.CANCELLED: "CANCELLED",
    ReservationStatus.EXPIRED: "CANCELLED",
}


@dataclass(frozen=True)
class CalendarEvent:
    reservation_id: uuid.UUID
    status: ReservationStatus
    starts_at: datetime
    ends_at: datetime
    hostname: str
    lab_name: str
    lab_location: str
    gpu_count: int
    gpu_numbers: Sequence[int]
    purpose: str
    # Increases each time the reservation changes, so calendars replace the old copy.
    sequence: int


def escape_text(value: str) -> str:
    """TEXT escaping (RFC 5545 3.3.11): backslash, semicolon, comma, and newlines."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Fold a content line so no physical line exceeds 75 octets (RFC 5545 3.1).

    Continuation lines start with one space, which counts toward their 75 octets.
    Splits happen between characters, never inside a multi-byte UTF-8 sequence.
    """
    parts: list[str] = []
    current = ""
    current_octets = 0
    for char in line:
        size = len(char.encode("utf-8"))
        if current_octets + size > MAX_LINE_OCTETS:
            parts.append(current)
            current, current_octets = " ", 1
        current += char
        current_octets += size
    parts.append(current)
    return CRLF.join(parts)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def reservation_ics(event: CalendarEvent, *, now: datetime) -> str:
    gpus = "GPU" if event.gpu_count == 1 else "GPUs"
    numbers = ", ".join(str(n) for n in event.gpu_numbers)
    location = f"{event.lab_name}, {event.lab_location}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{event.reservation_id}@{UID_DOMAIN}",
        f"DTSTAMP:{_utc(now)}",
        f"DTSTART:{_utc(event.starts_at)}",
        f"DTEND:{_utc(event.ends_at)}",
        f"SEQUENCE:{event.sequence}",
        f"STATUS:{_ICAL_STATUS[event.status]}",
        f"SUMMARY:{escape_text(f'{event.gpu_count} {gpus} on {event.hostname}')}",
        f"LOCATION:{escape_text(location)}",
        f"DESCRIPTION:{escape_text(f'GPUs {numbers}. Purpose: {event.purpose}')}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(fold_line(line) + CRLF for line in lines)


async def reservation_calendar(
    session: AsyncSession, viewer: User, reservation_id: uuid.UUID, *, now: datetime
) -> str:
    """The calendar file for the viewer's own reservation. Anyone else, admins
    included, gets ReservationNotFound: the file is the owner's copy of their booking.
    """
    reservation = await load_reservation(session, reservation_id)
    if reservation.user_id != viewer.id:
        raise ReservationNotFound(f"No reservation with id {reservation_id}.")
    changes = await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.entity_type == ENTITY_RESERVATION,
            AuditLog.entity_id == reservation.id,
        )
    )
    event = CalendarEvent(
        reservation_id=reservation.id,
        status=reservation.status,
        starts_at=reservation.starts_at,
        ends_at=reservation.ends_at,
        hostname=reservation.server.hostname,
        lab_name=reservation.server.lab.name,
        lab_location=reservation.server.lab.location,
        gpu_count=reservation.gpu_count,
        gpu_numbers=sorted(a.gpu.index for a in reservation.allocations),
        purpose=reservation.purpose,
        # The creation is the first audited change; every later one bumps the sequence.
        sequence=max((changes or 1) - 1, 0),
    )
    return reservation_ics(event, now=now)
