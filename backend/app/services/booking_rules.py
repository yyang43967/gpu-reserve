"""Booking rules that need no database: time grid, horizon, duration, peak hours, and
weekly quota windows.

Times are aware datetimes. Durations are counted in whole minutes, and GPU time in
GPU-minutes, so every comparison is exact.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from app.core.exceptions import (
    BookingTooLong,
    BookingTooShort,
    InvalidTimeGrid,
    InvalidTimeRange,
    PeakHoursLimit,
    QuotaExceeded,
    StartInPast,
    StartTooFar,
    TooManyGpus,
)

SLOT: Final = timedelta(minutes=30)
SLOT_SECONDS: Final = int(SLOT.total_seconds())
BOOKING_HORIZON: Final = timedelta(days=14)
MIN_DURATION: Final = SLOT
PEAK_START: Final = time(9, 0)
PEAK_END: Final = time(18, 0)
# Monday to Friday, as date.weekday() numbers them.
PEAK_WEEKDAYS: Final = frozenset(range(5))
PEAK_GPU_HOUR_LIMIT: Final = 4
MINUTES_PER_HOUR: Final = 60


@dataclass(frozen=True)
class Interval:
    """A half-open time range [start, end)."""

    start: datetime
    end: datetime

    def overlap_minutes(self, other: "Interval") -> int:
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return _minutes(end - start) if end > start else 0

    @property
    def minutes(self) -> int:
        return _minutes(self.end - self.start)


@dataclass(frozen=True)
class Booking:
    """GPU time someone holds: `gpu_count` GPUs over `interval`."""

    interval: Interval
    gpu_count: int


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds()) // 60


def _hours(minutes: int) -> str:
    """Human-readable hours: 13, 6.5."""
    return f"{minutes / MINUTES_PER_HOUR:g}"


# --- request shape ---


def normalize_range(starts_at: datetime, ends_at: datetime) -> Interval:
    """Convert to UTC and require end after start. Naive times are rejected by the API
    schema before they get here."""
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("times must be timezone-aware")
    interval = Interval(starts_at.astimezone(UTC), ends_at.astimezone(UTC))
    if interval.end <= interval.start:
        raise InvalidTimeRange("The end time must be after the start time.")
    return interval


# --- the 30-minute grid (UTC) ---


def is_on_grid(moment: datetime) -> bool:
    """Whole multiples of 30 minutes since the Unix epoch, with no seconds."""
    return moment.microsecond == 0 and int(moment.timestamp()) % SLOT_SECONDS == 0


def floor_to_grid(moment: datetime) -> datetime:
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % SLOT_SECONDS, UTC)


def ceil_to_grid(moment: datetime) -> datetime:
    """The first grid boundary at or after `moment`."""
    floor = floor_to_grid(moment)
    return floor if floor == moment else floor + SLOT


def free_per_slot(window: Interval, gpu_count: int, busy: Sequence[Interval]) -> list[int]:
    """Free GPUs in each grid slot of `window`, given the allocations that hold GPUs.

    `window` is on the grid. An active GPU has at most one active allocation at any
    instant, so counting the allocations that overlap a slot counts its busy GPUs.
    A difference array keeps this linear in slots plus allocations.
    """
    slots = window.minutes // (SLOT_SECONDS // 60)
    change = [0] * (slots + 1)
    for interval in busy:
        start = max(interval.start, window.start)
        end = min(interval.end, window.end)
        if end <= start:
            continue
        first = int((start - window.start).total_seconds()) // SLOT_SECONDS
        # Allocations are on the grid; rounding up covers a partial last slot anyway.
        last = -(-int((end - window.start).total_seconds()) // SLOT_SECONDS)
        change[first] += 1
        change[last] -= 1
    free: list[int] = []
    held = 0
    for slot in range(slots):
        held += change[slot]
        free.append(gpu_count - held)
    return free


# --- rules that need the server's settings ---


def check_times(interval: Interval, *, now: datetime, max_booking_hours: int) -> None:
    if not (is_on_grid(interval.start) and is_on_grid(interval.end)):
        raise InvalidTimeGrid("Start and end times must be on the hour or half hour.")
    if interval.start <= now:
        raise StartInPast("The start time must be in the future.")
    if interval.start > now + BOOKING_HORIZON:
        raise StartTooFar(f"Bookings can start at most {BOOKING_HORIZON.days} days ahead.")
    if interval.minutes < _minutes(MIN_DURATION):
        raise BookingTooShort(f"Bookings last at least {_minutes(MIN_DURATION)} minutes.")
    if interval.minutes > max_booking_hours * MINUTES_PER_HOUR:
        raise BookingTooLong(f"Bookings on this server last at most {max_booking_hours} hours.")


def check_gpu_count(gpu_count: int, *, available: int, hostname: str) -> None:
    if gpu_count > available:
        raise TooManyGpus(f"{hostname} has {available} GPUs; {gpu_count} were requested.")


def _local_days(interval: Interval, zone: ZoneInfo) -> Iterable[date]:
    day = interval.start.astimezone(zone).date()
    last = interval.end.astimezone(zone).date()
    while day <= last:
        yield day
        day += timedelta(days=1)


def peak_minutes(interval: Interval, zone: ZoneInfo) -> int:
    """Minutes of `interval` inside weekday peak hours in `zone`.

    Each day's window is built from local wall times, so it follows the zone's offset
    on that day, including across daylight saving changes.
    """
    total = 0
    for day in _local_days(interval, zone):
        if day.weekday() not in PEAK_WEEKDAYS:
            continue
        window = Interval(
            datetime.combine(day, PEAK_START, tzinfo=zone),
            datetime.combine(day, PEAK_END, tzinfo=zone),
        )
        total += interval.overlap_minutes(window)
    return total


def check_peak_hours(booking: Booking, zone: ZoneInfo) -> None:
    gpu_minutes = booking.gpu_count * peak_minutes(booking.interval, zone)
    if gpu_minutes > PEAK_GPU_HOUR_LIMIT * MINUTES_PER_HOUR:
        raise PeakHoursLimit(
            f"A booking may use at most {PEAK_GPU_HOUR_LIMIT} GPU-hours during peak hours "
            f"(weekdays {PEAK_START:%H:%M}-{PEAK_END:%H:%M} lab time); "
            f"this one uses {_hours(gpu_minutes)}."
        )


# --- weekly quota ---


def week_containing(moment: datetime, zone: ZoneInfo) -> Interval:
    """Monday 00:00 to the next Monday 00:00 in `zone`. A week with a daylight saving
    change is 167 or 169 hours long."""
    local_day = moment.astimezone(zone).date()
    monday = local_day - timedelta(days=local_day.weekday())
    start = datetime.combine(monday, time(0), tzinfo=zone)
    end = datetime.combine(monday + timedelta(days=7), time(0), tzinfo=zone)
    return Interval(start.astimezone(UTC), end.astimezone(UTC))


def weeks_overlapping(interval: Interval, zone: ZoneInfo) -> list[Interval]:
    weeks = [week_containing(interval.start, zone)]
    while weeks[-1].end < interval.end:
        weeks.append(week_containing(weeks[-1].end, zone))
    return weeks


def used_gpu_minutes(week: Interval, bookings: Iterable[Booking]) -> int:
    """GPU-minutes of `bookings` inside `week`, counting only the overlapping part."""
    return sum(b.gpu_count * b.interval.overlap_minutes(week) for b in bookings)


def check_quota(new: Booking, existing: list[Booking], *, quota_hours: int, zone: ZoneInfo) -> None:
    """Every week the new booking touches must stay within the quota."""
    limit = quota_hours * MINUTES_PER_HOUR
    for week in weeks_overlapping(new.interval, zone):
        used = used_gpu_minutes(week, existing)
        added = used_gpu_minutes(week, [new])
        if used + added > limit:
            week_label = week.start.astimezone(zone).date().isoformat()
            raise QuotaExceeded(
                f"This booking adds {_hours(added)} GPU-hours to the week of {week_label}, "
                f"where {_hours(used)} of your {quota_hours} are already used."
            )
