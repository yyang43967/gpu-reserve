"""The pure parts of slot search: free GPUs per slot, sliding windows, and spreading
results out."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.booking_rules import Interval, free_per_slot
from app.services.slot_search import feasible_starts, spread_out

T0 = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
SLOT = timedelta(minutes=30)


def at(slots: float) -> datetime:
    return T0 + slots * SLOT


# --- free GPUs per slot ---


def test_free_per_slot_counts_overlapping_allocations() -> None:
    window = Interval(at(0), at(6))
    busy = [Interval(at(1), at(3)), Interval(at(2), at(5)), Interval(at(-4), at(1))]

    assert free_per_slot(window, gpu_count=4, busy=busy) == [3, 3, 2, 3, 3, 4]


def test_allocations_outside_the_window_are_ignored() -> None:
    window = Interval(at(0), at(2))
    busy = [Interval(at(-3), at(0)), Interval(at(2), at(9))]

    assert free_per_slot(window, gpu_count=2, busy=busy) == [2, 2]


def test_a_half_open_end_frees_the_slot_it_ends_on() -> None:
    window = Interval(at(0), at(2))

    assert free_per_slot(window, gpu_count=1, busy=[Interval(at(0), at(1))]) == [0, 1]


# --- sliding windows ---


def test_feasible_starts_need_enough_gpus_in_every_slot() -> None:
    free = [4, 4, 1, 4, 4, 4, 2]

    # Two GPUs for two slots: slot 2 has only one free.
    assert feasible_starts(free, needed=2, length=2) == [(0, 4), (3, 4), (4, 4), (5, 2)]


def test_feasible_starts_report_the_tightest_slot() -> None:
    assert feasible_starts([3, 1, 2], needed=1, length=3) == [(0, 1)]


def test_no_start_when_the_window_is_shorter_than_the_booking() -> None:
    assert feasible_starts([4, 4], needed=1, length=3) == []


@pytest.mark.parametrize("length", [1, 2, 5])
def test_feasible_starts_match_a_brute_force_check(length: int) -> None:
    free = [3, 0, 2, 4, 4, 1, 4, 4, 4, 2, 0, 3, 3, 3]
    needed = 2

    expected = [
        (i, min(free[i : i + length]))
        for i in range(len(free) - length + 1)
        if min(free[i : i + length]) >= needed
    ]

    assert feasible_starts(free, needed=needed, length=length) == expected


# --- spreading results out ---


def test_spread_out_picks_starts_that_do_not_overlap() -> None:
    # Two-slot bookings could start at 0, 1, 2, 3, 6: picking 0 rules out 1, then 2.
    assert spread_out([0, 1, 2, 3, 6], length=2, limit=3) == [0, 2, 6]


def test_spread_out_stops_at_the_limit() -> None:
    assert spread_out([0, 2, 4, 6, 8], length=2, limit=3) == [0, 2, 4]


def test_spread_out_of_nothing() -> None:
    assert spread_out([], length=2, limit=3) == []
