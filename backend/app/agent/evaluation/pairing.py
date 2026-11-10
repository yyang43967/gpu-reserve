"""Turning runs into per-case outcomes that two configurations can be compared on.

A configuration runs every case twice. A case passes for that configuration only if it
passed in both runs (strict), which keeps a lucky draw from counting as a win. A case
left out of any run of any configuration being compared (an infrastructure failure or
an aborted run) is dropped from every comparison, so all pairwise tests are on the
same cases; the number dropped is reported with them.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

EXCLUDED_STATUSES: Final = frozenset({"infra", "aborted"})

Rows = Sequence[Mapping[str, Any]]


def strict_outcomes(runs: Sequence[Rows]) -> dict[str, bool | None]:
    """Case id -> passed in every run, or None when it was left out of any run."""
    outcomes: dict[str, bool | None] = {}
    for rows in runs:
        for row in rows:
            case_id = str(row["id"])
            if row["status"] in EXCLUDED_STATUSES:
                outcomes[case_id] = None
            elif case_id not in outcomes:
                outcomes[case_id] = bool(row["passed"])
            elif outcomes[case_id] is not None:
                outcomes[case_id] = bool(outcomes[case_id]) and bool(row["passed"])
    case_sets = [{str(row["id"]) for row in rows} for rows in runs]
    everywhere = set(case_sets[0]).intersection(*case_sets[1:]) if case_sets else set()
    for case_id in set(outcomes) - everywhere:
        outcomes[case_id] = None  # missing from a run counts as left out
    return outcomes


@dataclass(frozen=True)
class Common:
    cases: list[str]
    # Case id -> the configurations it was left out of.
    dropped: dict[str, list[str]]


def common_cases(configs: Mapping[str, Mapping[str, bool | None]]) -> Common:
    """The cases every configuration has an outcome for."""
    ids = sorted(set().union(*(set(o) for o in configs.values())))
    dropped: dict[str, list[str]] = {}
    for case_id in ids:
        missing = sorted(name for name, o in configs.items() if o.get(case_id) is None)
        if missing:
            dropped[case_id] = missing
    return Common([i for i in ids if i not in dropped], dropped)


@dataclass(frozen=True)
class Pair:
    both: int
    only_a: int
    only_b: int
    neither: int


def pair(a: Mapping[str, bool | None], b: Mapping[str, bool | None], cases: Sequence[str]) -> Pair:
    counts = {"both": 0, "only_a": 0, "only_b": 0, "neither": 0}
    for case_id in cases:
        x, y = bool(a[case_id]), bool(b[case_id])
        key = "both" if x and y else "only_a" if x else "only_b" if y else "neither"
        counts[key] += 1
    return Pair(**counts)


# --- statistics ---


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar test: a binomial test on the discordant cases."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (1 << n)
    return min(1.0, 2 * tail)


def holm(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values, in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def smallest_significant_splits(
    alpha: float, tests: int, max_discordant: int
) -> list[tuple[int, int]]:
    """For each number of discordant cases, the most even split that would still pass
    the strictest Holm threshold (alpha / tests). Shows what the case count can detect."""
    threshold = alpha / tests
    splits: list[tuple[int, int]] = []
    for n in range(1, max_discordant + 1):
        for minority in range(n // 2, -1, -1):
            if mcnemar_exact(n - minority, minority) < threshold:
                splits.append((n - minority, minority))
                break
    return splits


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between closest ranks; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
