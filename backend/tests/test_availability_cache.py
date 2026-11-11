"""Pure parts of the availability cache: day buckets, merging, encoding, keys."""

import uuid
from datetime import UTC, date, datetime, timedelta, timezone

from app.core.config import Settings
from app.services.availability_cache import (
    ENTRY_FORMAT_VERSION,
    Hold,
    build_availability_cache,
    day_key,
    days_covering,
    decode,
    encode,
    generation_key,
    merge,
    split_by_day,
)
from app.services.booking_rules import Interval

H = timedelta(hours=1)
MIDNIGHT = datetime(2030, 1, 13, tzinfo=UTC)
SERVER = uuid.UUID("00000000-0000-0000-0000-000000000001")


def hold(start: datetime, hours: float = 1, *, id_: int = 1) -> Hold:
    return Hold(uuid.UUID(int=id_), start, start + timedelta(hours=hours))


def test_a_window_inside_one_day_covers_that_day() -> None:
    assert days_covering(Interval(MIDNIGHT + H, MIDNIGHT + 2 * H)) == [date(2030, 1, 13)]


def test_an_end_at_midnight_does_not_touch_the_next_day() -> None:
    assert days_covering(Interval(MIDNIGHT - H, MIDNIGHT)) == [date(2030, 1, 12)]


def test_a_window_across_midnight_covers_both_days() -> None:
    assert days_covering(Interval(MIDNIGHT - H, MIDNIGHT + H)) == [
        date(2030, 1, 12),
        date(2030, 1, 13),
    ]


def test_days_are_utc_days_whatever_the_offset() -> None:
    pacific = timezone(timedelta(hours=-8))
    # 20:00 to 23:00 on the 12th in UTC-8 is 04:00 to 07:00 on the 13th in UTC.
    start = datetime(2030, 1, 12, 20, 0, tzinfo=pacific)
    assert days_covering(Interval(start, start + 3 * H)) == [date(2030, 1, 13)]


def test_a_fourteen_day_window_covers_fifteen_days_when_not_aligned() -> None:
    start = MIDNIGHT + 10 * H
    assert len(days_covering(Interval(start, start + timedelta(days=14)))) == 15


def test_a_hold_across_midnight_goes_into_both_days() -> None:
    late = hold(MIDNIGHT - H, hours=2)
    early = hold(MIDNIGHT + 3 * H, id_=2)
    by_day = split_by_day([late, early], [date(2030, 1, 12), date(2030, 1, 13)])
    assert by_day == {date(2030, 1, 12): [late], date(2030, 1, 13): [late, early]}


def test_merging_counts_a_multi_day_hold_once() -> None:
    late = hold(MIDNIGHT - H, hours=26)
    window = Interval(MIDNIGHT - 2 * H, MIDNIGHT + 2 * H + timedelta(days=1))
    assert merge([[late], [late], [late]], window) == [late]


def test_merging_keeps_distinct_holds_with_the_same_times() -> None:
    # Two GPUs of one booking: same times, different allocations, both busy.
    first, second = hold(MIDNIGHT, id_=1), hold(MIDNIGHT, id_=2)
    assert merge([[first, second]], Interval(MIDNIGHT, MIDNIGHT + H)) == [first, second]


def test_merging_drops_holds_outside_the_window() -> None:
    inside, before, after = (
        hold(MIDNIGHT + H),
        hold(MIDNIGHT - H, id_=2),
        hold(MIDNIGHT + 3 * H, id_=3),
    )
    window = Interval(MIDNIGHT, MIDNIGHT + 3 * H)
    assert merge([[before, inside, after]], window) == [inside]


def test_entries_round_trip() -> None:
    holds = [hold(MIDNIGHT, id_=1), hold(MIDNIGHT + H, hours=2.5, id_=2)]
    assert decode(encode(holds)) == holds
    assert decode(encode([])) == []


def test_keys_carry_the_entry_format_version() -> None:
    prefix = f"availability:v{ENTRY_FORMAT_VERSION}:{SERVER}"
    assert generation_key(SERVER) == f"{prefix}:generation"
    assert day_key(SERVER, 7, date(2030, 1, 13)) == f"{prefix}:7:2030-01-13"


def test_the_cache_can_be_turned_off() -> None:
    off = Settings(jwt_secret="x" * 32, availability_cache_enabled=False)
    assert build_availability_cache(off, redis=None) is None  # type: ignore[arg-type]
    on = Settings(jwt_secret="x" * 32)
    assert on.availability_cache_enabled is True
    assert build_availability_cache(on, redis=None) is not None  # type: ignore[arg-type]
