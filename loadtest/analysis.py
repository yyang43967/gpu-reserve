"""Reading k6 summaries: shared by run.py (to pick the fixed rates) and report.py."""

import argparse
import re
import statistics
from typing import Any

CONFIGS = ("cache-off", "cache-on")
TIMED_SCRIPTS = ("browse_servers", "availability", "slot_search")
# browse_servers makes two requests per iteration (the list, then one server).
REQUESTS_PER_ITERATION = {"browse_servers": 2, "availability": 1, "slot_search": 1}
# The capacity ramp's last step, in iterations per second: about twice the saturated
# throughput seen in trial runs, so every configuration reaches its limit.
RAMP_MAX = {"browse_servers": 250, "availability": 450, "slot_search": 45}
# A step counts as sustained if p95 stays under this, almost everything offered was
# completed, and almost nothing failed.
SLO_P95_MS = 500.0
SUSTAINED_SHARE = 0.95
SUSTAINED_MAX_ERRORS = 0.01
# The fixed rate for the latency comparison: this share of the median uncached rate
# sustained cleanly (as above, but with no failed request at all). Not the peak, which
# counts failures, so a stack shedding load with fast 503s would set it too high.
LATENCY_LOAD_SHARE = 0.65
# Status codes counted per tag by the k6 scripts (lib/common.js).
STATUSES = ("0", "200", "201", "400", "401", "403", "404", "409", "422", "429", "500", "502")
STATUSES += ("503", "504", "other")


def seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m)", duration)
    if not match:
        raise argparse.ArgumentTypeError("use a duration like 30s or 2m")
    value, unit = int(match[1]), match[2]
    return value * 60 if unit == "m" else value


def metric(summary: dict[str, Any], name: str, stat: str) -> float:
    found = summary["metrics"].get(name)
    if found is None:
        return 0.0
    return float(found["values"].get(stat, 0.0))


def steps(
    summary: dict[str, Any], *, script: str, ramp_max: float, step_count: int, hold: int
) -> list[dict[str, float]]:
    """Per capacity step: offered and achieved requests per second, p95, and errors."""
    per_iteration = REQUESTS_PER_ITERATION[script]
    rows = []
    for step in range(1, step_count + 1):
        tag = f"step:{step}"
        rows.append(
            {
                "step": step,
                "offered_rps": ramp_max * step / step_count * per_iteration,
                "achieved_rps": metric(summary, f"http_reqs{{{tag}}}", "count") / hold,
                "p95": metric(summary, f"http_req_duration{{{tag}}}", "p(95)"),
                "failed": metric(summary, f"http_req_failed{{{tag}}}", "rate"),
            }
        )
    return rows


def statuses(summary: dict[str, Any], tag: str) -> dict[str, int]:
    """Responses by status code for one tag, such as `phase:measure` or `step:3`."""
    counts = {}
    for status in STATUSES:
        count = int(metric(summary, f"responses{{{tag},status:{status}}}", "count"))
        if count:
            counts[status] = count
    return counts


def sustained_rps(rows: list[dict[str, float]], *, max_errors: float) -> float:
    """The highest offered rate before the first step that misses the target."""
    sustained = 0.0
    for row in rows:
        if (
            row["p95"] <= SLO_P95_MS
            and row["achieved_rps"] >= SUSTAINED_SHARE * row["offered_rps"]
            and row["failed"] <= max_errors
        ):
            sustained = row["offered_rps"]
        else:
            break
    return sustained


def capacity(rows: list[dict[str, float]]) -> dict[str, float]:
    """The peak throughput reached, and the highest offered rate that was sustained."""
    peak = max(r["achieved_rps"] for r in rows)
    sustained = sustained_rps(rows, max_errors=SUSTAINED_MAX_ERRORS)
    top = rows[-1]
    return {
        "peak_rps": peak,
        "sustained_rps": sustained,
        "clean_sustained_rps": sustained_rps(rows, max_errors=0.0),
        # The ramp never saturated the stack: the limit is above RAMP_MAX. Fast
        # failures keep achieved close to offered, so errors count as saturation.
        "limit_not_reached": top["achieved_rps"] >= SUSTAINED_SHARE * top["offered_rps"]
        and top["failed"] <= SUSTAINED_MAX_ERRORS,
    }


def median(values: list[float]) -> float:
    return statistics.median(values)


def pooled_sustained(runs: list[list[dict[str, float]]]) -> float:
    """The sustained rate across repeats: per step, the median of each figure over the
    runs, then the highest step whose medians pass. One run's noisy step can end that
    run's own sustained rate early; the median step is less sensitive to it."""
    passing = [0.0]
    for index in range(len(runs[0])):
        step = {key: median([run[index][key] for run in runs]) for key in runs[0][index]}
        if (
            step["p95"] <= SLO_P95_MS
            and step["achieved_rps"] >= SUSTAINED_SHARE * step["offered_rps"]
            and step["failed"] <= SUSTAINED_MAX_ERRORS
        ):
            passing.append(step["offered_rps"])
    return max(passing)


MEMORY_UNITS = {
    "B": 1 / 2**20,
    "KiB": 1 / 2**10,
    "MiB": 1.0,
    "GiB": 2**10,
    "kB": 1e3 / 2**20,
    "MB": 1e6 / 2**20,
    "GB": 1e9 / 2**20,
}


def memory_mib(usage: str) -> float:
    """MiB from docker stats' MemUsage, e.g. "219.2MiB / 448MiB" -> 219.2."""
    match = re.match(r"\s*([\d.]+)\s*([KMG]i?B|[kMG]B|B)", usage)
    if not match:
        raise ValueError(f"unrecognized memory usage: {usage!r}")
    return float(match[1]) * MEMORY_UNITS[match[2]]


def memory_trend(
    samples: list[dict[str, Any]], service: str, load_start: float, load_seconds: float
) -> dict[str, float]:
    """Where a container's memory stood at the start of the load, at its end, and after
    it settled, and how fast it grew over the second half of the load (MiB per 10
    minutes, least squares). Steady growth through the second half suggests a leak;
    a slope near zero after an early rise is warm-up."""
    rows = [(s["t"] - load_start, s["mem_mib"]) for s in samples if s["service"] == service]
    load_end = load_seconds

    def window(start: float, end: float) -> float:
        values = [mib for t, mib in rows if start <= t < end]
        return statistics.median(values) if values else float("nan")

    second_half = [(t, mib) for t, mib in rows if load_end / 2 <= t < load_end]
    slope = float("nan")
    if len(second_half) >= 2:
        times = [t for t, _ in second_half]
        values = [mib for _, mib in second_half]
        slope = statistics.linear_regression(times, values).slope * 600
    last = max((t for t, _ in rows), default=load_end)
    return {
        "first_minute_mib": window(0, 60),
        "last_minute_of_load_mib": window(load_end - 60, load_end),
        "after_settling_mib": window(last - 60, last + 1),
        "peak_mib": max((mib for _, mib in rows), default=float("nan")),
        "second_half_slope_mib_per_10min": slope,
    }
