"""The iCalendar file for one reservation (RFC 5545), without a database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import ReservationStatus
from app.services.calendar import CalendarEvent, escape_text, fold_line, reservation_ics

START = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
STAMP = datetime(2030, 1, 7, 18, 4, 5, tzinfo=UTC)
RESERVATION_ID = uuid.UUID("6f1c2a7e-3b9d-4c55-9a0e-2d8f1b7c4e10")


def event(**overrides: object) -> CalendarEvent:
    values: dict[str, object] = {
        "reservation_id": RESERVATION_ID,
        "status": ReservationStatus.APPROVED,
        "starts_at": START,
        "ends_at": START + timedelta(hours=2),
        "hostname": "a100-node-01",
        "lab_name": "Vision Lab",
        "lab_location": "Building 4, room 210",
        "gpu_count": 2,
        "gpu_numbers": [0, 1],
        "purpose": "Fine-tuning a 7B model",
        "sequence": 0,
    }
    values.update(overrides)
    return CalendarEvent(**values)  # type: ignore[arg-type]


def unfold(body: str) -> list[str]:
    """Undo line folding: a CRLF followed by one space joins the lines."""
    return body.replace("\r\n ", "").split("\r\n")


def test_a_complete_calendar_with_crlf_line_endings() -> None:
    body = reservation_ics(event(), now=STAMP)

    assert body.endswith("\r\n")
    assert "\n" not in body.replace("\r\n", "")
    lines = unfold(body)
    assert lines[:4] == [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GPU Reserve//Reservations//EN",
        "CALSCALE:GREGORIAN",
    ]
    assert lines[-2:] == ["END:VCALENDAR", ""]
    assert "BEGIN:VEVENT" in lines and "END:VEVENT" in lines
    assert f"UID:{RESERVATION_ID}@gpu-reserve" in lines
    assert "DTSTAMP:20300107T180405Z" in lines
    assert "DTSTART:20300112T160000Z" in lines
    assert "DTEND:20300112T180000Z" in lines
    assert "SEQUENCE:0" in lines
    assert "STATUS:CONFIRMED" in lines
    assert "SUMMARY:2 GPUs on a100-node-01" in lines
    assert "LOCATION:Vision Lab\\, Building 4\\, room 210" in lines


@pytest.mark.parametrize(
    ("status", "ical"),
    [
        (ReservationStatus.PENDING, "TENTATIVE"),
        (ReservationStatus.APPROVED, "CONFIRMED"),
        (ReservationStatus.COMPLETED, "CONFIRMED"),
        (ReservationStatus.REJECTED, "CANCELLED"),
        (ReservationStatus.CANCELLED, "CANCELLED"),
        (ReservationStatus.EXPIRED, "CANCELLED"),
    ],
)
def test_status_maps_to_ical(status: ReservationStatus, ical: str) -> None:
    assert f"STATUS:{ical}" in unfold(reservation_ics(event(status=status), now=STAMP))


def test_one_gpu_is_singular() -> None:
    lines = unfold(reservation_ics(event(gpu_count=1, gpu_numbers=[3]), now=STAMP))

    assert "SUMMARY:1 GPU on a100-node-01" in lines


def test_sequence_is_carried_through() -> None:
    assert "SEQUENCE:3" in unfold(reservation_ics(event(sequence=3), now=STAMP))


def test_description_lists_purpose_and_gpu_numbers() -> None:
    lines = unfold(reservation_ics(event(purpose="Line one\nLine two"), now=STAMP))

    [description] = [line for line in lines if line.startswith("DESCRIPTION:")]
    assert description == "DESCRIPTION:GPUs 0\\, 1. Purpose: Line one\\nLine two"


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("a,b;c", "a\\,b\\;c"),
        ("back\\slash", "back\\\\slash"),
        ("one\r\ntwo\nthree", "one\\ntwo\\nthree"),
        ("plain", "plain"),
    ],
)
def test_text_escaping(raw: str, escaped: str) -> None:
    assert escape_text(raw) == escaped


def test_short_lines_are_not_folded() -> None:
    assert fold_line("SUMMARY:short") == "SUMMARY:short"


def test_long_ascii_lines_fold_at_75_octets() -> None:
    line = "DESCRIPTION:" + "x" * 200

    folded = fold_line(line)

    parts = folded.split("\r\n")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert all(part.startswith(" ") for part in parts[1:])
    assert folded.replace("\r\n ", "") == line


def test_folding_never_splits_a_multibyte_character() -> None:
    """Ü and ø are 2 bytes in UTF-8, ✓ is 3, and 研 and 究 are 3 each. A fold at a fixed
    byte offset would cut some of them in half."""
    line = "DESCRIPTION:" + "Ünïcødé ✓ 研究 " * 20

    folded = fold_line(line)

    parts = folded.split("\r\n")
    for part in parts:
        encoded = part.encode("utf-8")
        assert len(encoded) <= 75
        assert encoded.decode("utf-8") == part  # each part is valid UTF-8 on its own
    assert folded.replace("\r\n ", "") == line


def test_every_line_of_a_full_calendar_is_within_75_octets() -> None:
    body = reservation_ics(event(purpose="研究 " * 100, lab_name="Ünïcødé Lab"), now=STAMP)

    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert "研究 研究" in "".join(unfold(body))
