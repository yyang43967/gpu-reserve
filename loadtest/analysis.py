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
# The fixed rate for the latency comparison, as a share of the uncached capacity.
LATENCY_LOAD_SHARE = 0.65


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


def capacity(rows: list[dict[str, float]]) -> dict[str, float]:
    """The peak throughput reached, and the highest offered rate that was sustained."""
    peak = max(r["achieved_rps"] for r in rows)
    sustained = 0.0
    for row in rows:
        if (
            row["p95"] <= SLO_P95_MS
            and row["achieved_rps"] >= SUSTAINED_SHARE * row["offered_rps"]
            and row["failed"] <= SUSTAINED_MAX_ERRORS
        ):
            sustained = row["offered_rps"]
        else:
            break
    top = rows[-1]
    return {
        "peak_rps": peak,
        "sustained_rps": sustained,
        # The ramp never saturated the stack: the limit is above RAMP_MAX.
        "limit_not_reached": top["achieved_rps"] >= SUSTAINED_SHARE * top["offered_rps"],
    }


def median(values: list[float]) -> float:
    return statistics.median(values)
