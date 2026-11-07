from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

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
from app.services import booking_rules
from app.services.booking_rules import (
    Booking,
    Interval,
    ceil_to_grid,
    check_gpu_count,
    check_peak_hours,
    check_quota,
    check_times,
    floor_to_grid,
    is_on_grid,
    normalize_range,
    peak_minutes,
    used_gpu_minutes,
    week_containing,
    weeks_overlapping,
)

LA = ZoneInfo("America/Los_Angeles")
TOKYO = ZoneInfo("Asia/Tokyo")
KOLKATA = ZoneInfo("Asia/Kolkata")
H = timedelta(hours=1)
M = timedelta(minutes=1)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def local(zone: ZoneInfo, *args: int) -> datetime:
    return datetime(*args, tzinfo=zone)  # type: ignore[misc]


# --- request shape ---


def test_normalize_range_converts_to_utc() -> None:
    interval = normalize_range(local(LA, 2030, 1, 7, 10), local(LA, 2030, 1, 7, 12))

    assert interval == Interval(utc(2030, 1, 7, 18), utc(2030, 1, 7, 20))
    assert interval.start.tzinfo is UTC


@pytest.mark.parametrize("length", [timedelta(0), -H])
def test_normalize_range_requires_end_after_start(length: timedelta) -> None:
    start = utc(2030, 1, 7, 18)
    with pytest.raises(InvalidTimeRange):
        normalize_range(start, start + length)


def test_normalize_range_rejects_naive_times() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_range(datetime(2030, 1, 7, 18), datetime(2030, 1, 7, 19))


# --- grid ---


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (utc(2030, 1, 7, 18, 0), True),
        (utc(2030, 1, 7, 18, 30), True),
        (utc(2030, 1, 7, 18, 15), False),
        (utc(2030, 1, 7, 18, 0, 1), False),
        (utc(2030, 1, 7, 18, 0, 0, 1), False),
        # Same instant expressed with offsets: the grid is absolute.
        (local(LA, 2030, 1, 7, 10, 30), True),
        (local(KOLKATA, 2030, 1, 7, 10, 0), True),  # 04:30 UTC
        (local(KOLKATA, 2030, 1, 7, 10, 15), False),
    ],
)
def test_is_on_grid(moment: datetime, expected: bool) -> None:
    assert is_on_grid(moment) is expected


@pytest.mark.parametrize(
    ("moment", "floor", "ceil"),
    [
        (utc(2030, 1, 7, 18, 0), utc(2030, 1, 7, 18, 0), utc(2030, 1, 7, 18, 0)),
        (utc(2030, 1, 7, 18, 0, 0, 1), utc(2030, 1, 7, 18, 0), utc(2030, 1, 7, 18, 30)),
        (utc(2030, 1, 7, 18, 29, 59), utc(2030, 1, 7, 18, 0), utc(2030, 1, 7, 18, 30)),
        (utc(2030, 1, 7, 23, 45), utc(2030, 1, 7, 23, 30), utc(2030, 1, 8, 0, 0)),
    ],
)
def test_grid_rounding(moment: datetime, floor: datetime, ceil: datetime) -> None:
    assert floor_to_grid(moment) == floor
    assert ceil_to_grid(moment) == ceil


# --- times ---

NOW = utc(2030, 1, 7, 12, 10)


def _check(start: datetime, end: datetime, max_hours: int = 12) -> None:
    check_times(Interval(start, end), now=NOW, max_booking_hours=max_hours)


def test_valid_times_pass() -> None:
    _check(utc(2030, 1, 7, 12, 30), utc(2030, 1, 7, 13))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (utc(2030, 1, 7, 13, 15), utc(2030, 1, 7, 14)),
        (utc(2030, 1, 7, 13), utc(2030, 1, 7, 14, 10)),
    ],
)
def test_off_grid_times_are_rejected(start: datetime, end: datetime) -> None:
    with pytest.raises(InvalidTimeGrid):
        _check(start, end)


@pytest.mark.parametrize("start", [utc(2030, 1, 7, 12), utc(2030, 1, 7, 11, 30)])
def test_start_must_be_in_the_future(start: datetime) -> None:
    with pytest.raises(StartInPast):
        _check(start, start + H)


def test_start_exactly_now_is_not_in_the_future() -> None:
    now = utc(2030, 1, 7, 12)
    with pytest.raises(StartInPast):
        check_times(Interval(now, now + H), now=now, max_booking_hours=12)


def test_horizon_is_fourteen_days() -> None:
    now = utc(2030, 1, 7, 12)
    last = now + timedelta(days=14)
    check_times(Interval(last, last + H), now=now, max_booking_hours=12)

    with pytest.raises(StartTooFar):
        check_times(Interval(last + 30 * M, last + 90 * M), now=now, max_booking_hours=12)


def test_duration_bounds() -> None:
    start = utc(2030, 1, 7, 13)
    _check(start, start + 30 * M)
    _check(start, start + 12 * H)

    with pytest.raises(BookingTooLong):
        _check(start, start + 12 * H + 30 * M)
    with pytest.raises(BookingTooLong):
        _check(start, start + 2 * H, max_hours=1)


def test_minimum_duration_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    """On the grid every valid range is already at least 30 minutes, so the rule is
    tested with a longer minimum to show it holds on its own."""
    monkeypatch.setattr(booking_rules, "MIN_DURATION", H)
    start = utc(2030, 1, 7, 13)

    with pytest.raises(BookingTooShort):
        _check(start, start + 30 * M)


def test_gpu_count_cannot_exceed_server() -> None:
    check_gpu_count(4, available=4, hostname="a100-node-01")
    with pytest.raises(TooManyGpus, match="a100-node-01 has 4 GPUs"):
        check_gpu_count(5, available=4, hostname="a100-node-01")


# --- peak hours ---


@pytest.mark.parametrize(
    ("start", "end", "zone", "minutes"),
    [
        # Monday 08:00-10:00 PST: one hour in peak.
        (local(LA, 2030, 1, 7, 8), local(LA, 2030, 1, 7, 10), LA, 60),
        # Entirely in the evening.
        (local(LA, 2030, 1, 7, 18), local(LA, 2030, 1, 7, 23), LA, 0),
        # Saturday daytime is off-peak.
        (local(LA, 2030, 1, 12, 9), local(LA, 2030, 1, 12, 18), LA, 0),
        # Friday 17:00 to Monday 10:00: one hour Friday, one hour Monday.
        (local(LA, 2030, 1, 11, 17), local(LA, 2030, 1, 14, 10), LA, 120),
        # Crossing midnight: Monday 17:00 to Tuesday 10:00.
        (local(LA, 2030, 1, 7, 17), local(LA, 2030, 1, 8, 10), LA, 120),
        # Tokyo has no daylight saving.
        (local(TOKYO, 2030, 7, 1, 8), local(TOKYO, 2030, 7, 1, 9, 30), TOKYO, 30),
    ],
)
def test_peak_minutes(start: datetime, end: datetime, zone: ZoneInfo, minutes: int) -> None:
    assert peak_minutes(Interval(start.astimezone(UTC), end.astimezone(UTC)), zone) == minutes


def test_peak_window_follows_spring_forward() -> None:
    """16:00-17:00 UTC is 08:00 PST before the change (Sunday 10 March 2030) and
    09:00 PDT after it."""
    before = Interval(utc(2030, 3, 8, 16), utc(2030, 3, 8, 17))
    after = Interval(utc(2030, 3, 11, 16), utc(2030, 3, 11, 17))

    assert peak_minutes(before, LA) == 0
    assert peak_minutes(after, LA) == 60


def test_peak_window_follows_fall_back() -> None:
    """01:00-02:00 UTC is 18:00 PDT on Friday 1 November 2030, after peak hours, and
    17:00 PST on Monday 4 November, after the change on Sunday 3 November."""
    before = Interval(utc(2030, 11, 2, 1), utc(2030, 11, 2, 2))
    after = Interval(utc(2030, 11, 5, 1), utc(2030, 11, 5, 2))

    assert peak_minutes(before, LA) == 0  # 18:00-19:00 PDT Friday
    assert peak_minutes(after, LA) == 60  # 17:00-18:00 PST Monday


@pytest.mark.parametrize(
    ("gpu_count", "hours", "allowed"),
    [(1, 4, True), (2, 2, True), (4, 1, True), (1, 4.5, False), (8, 1, False), (3, 1.5, False)],
)
def test_peak_limit_is_four_gpu_hours(gpu_count: int, hours: float, allowed: bool) -> None:
    start = local(LA, 2030, 1, 7, 9).astimezone(UTC)
    booking = Booking(Interval(start, start + timedelta(hours=hours)), gpu_count)

    if allowed:
        check_peak_hours(booking, LA)
    else:
        with pytest.raises(PeakHoursLimit):
            check_peak_hours(booking, LA)


def test_off_peak_bookings_have_no_gpu_limit() -> None:
    start = local(LA, 2030, 1, 12, 8).astimezone(UTC)  # Saturday
    check_peak_hours(Booking(Interval(start, start + 12 * H), 8), LA)


# --- weeks ---


def test_week_containing_a_normal_week() -> None:
    week = week_containing(utc(2030, 1, 9, 20), LA)  # Wednesday

    assert week.start == local(LA, 2030, 1, 7, 0)
    assert week.end == local(LA, 2030, 1, 14, 0)
    assert week.end - week.start == 168 * H


def test_week_boundary_is_local_midnight() -> None:
    """Monday 07:59 UTC is still Sunday 23:59 in Los Angeles."""
    assert week_containing(utc(2030, 1, 14, 7, 59), LA).start == local(LA, 2030, 1, 7)
    assert week_containing(utc(2030, 1, 14, 8, 0), LA).start == local(LA, 2030, 1, 14)


def test_spring_forward_week_is_167_hours() -> None:
    week = week_containing(utc(2030, 3, 8, 12), LA)
    assert week.end - week.start == 167 * H


def test_fall_back_week_is_169_hours() -> None:
    week = week_containing(utc(2030, 10, 30, 12), LA)
    assert week.end - week.start == 169 * H


def test_weeks_overlapping_a_booking_across_monday() -> None:
    interval = Interval(local(LA, 2030, 1, 13, 20).astimezone(UTC), local(LA, 2030, 1, 14, 4))

    weeks = weeks_overlapping(interval, LA)

    assert [w.start for w in weeks] == [local(LA, 2030, 1, 7), local(LA, 2030, 1, 14)]


def test_booking_ending_at_monday_midnight_stays_in_one_week() -> None:
    interval = Interval(local(LA, 2030, 1, 13, 20).astimezone(UTC), local(LA, 2030, 1, 14, 0))
    assert len(weeks_overlapping(interval, LA)) == 1


# --- quota ---

WEEK = week_containing(utc(2030, 1, 9), LA)


def _booking(start: datetime, hours: float, gpus: int = 1) -> Booking:
    begin = start.astimezone(UTC)
    return Booking(Interval(begin, begin + timedelta(hours=hours)), gpus)


def test_used_minutes_count_only_the_overlap() -> None:
    bookings = [
        _booking(local(LA, 2030, 1, 8, 10), 2, gpus=2),  # inside: 240
        _booking(local(LA, 2030, 1, 6, 22), 4, gpus=1),  # 2 hours inside: 120
        _booking(local(LA, 2030, 1, 20, 10), 4, gpus=8),  # another week: 0
    ]
    assert used_gpu_minutes(WEEK, bookings) == 360


def test_quota_allows_up_to_the_limit() -> None:
    existing = [_booking(local(LA, 2030, 1, 8, 20), 10, gpus=4)]  # 40 hours
    check_quota(_booking(local(LA, 2030, 1, 10, 20), 8), existing, quota_hours=48, zone=LA)


def test_quota_rejects_going_over() -> None:
    existing = [_booking(local(LA, 2030, 1, 8, 20), 10, gpus=4)]
    with pytest.raises(QuotaExceeded, match="week of 2030-01-07"):
        check_quota(_booking(local(LA, 2030, 1, 10, 20), 8.5), existing, quota_hours=48, zone=LA)


def test_quota_checks_every_week_the_booking_touches() -> None:
    """Next week is full, so a booking that spills past Monday midnight is refused even
    though this week has room."""
    existing = [_booking(local(LA, 2030, 1, 15, 20), 12, gpus=4)]  # 48 hours next week
    new = _booking(local(LA, 2030, 1, 13, 22), 4)  # 2 hours this week, 2 next

    with pytest.raises(QuotaExceeded, match="week of 2030-01-14"):
        check_quota(new, existing, quota_hours=48, zone=LA)


def test_zero_quota_blocks_everything() -> None:
    with pytest.raises(QuotaExceeded):
        check_quota(_booking(local(LA, 2030, 1, 8, 20), 0.5), [], quota_hours=0, zone=LA)
