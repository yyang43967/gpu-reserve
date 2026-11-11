#!/usr/bin/env python3
"""Summarize a load-test output directory written by run.py, as Markdown.

    python3 loadtest/report.py loadtest/out/<run>

Each figure is the median over the repeats of one configuration; ranges across repeats
are shown where they matter. Standard library only.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from analysis import (
    CONFIGS,
    SLO_P95_MS,
    capacity,
    median,
    metric,
    seconds,
    statuses,
    steps,
)

# Samples this close to a phase boundary are dropped: k6 takes a moment to start, and
# docker stats averages over its own interval.
EDGE_SECONDS = 2

Runs = dict[tuple[str, str, str], list[dict[str, Any]]]


def load_runs(out: Path) -> Runs:
    runs: Runs = defaultdict(list)
    for config in CONFIGS:
        for run_dir in sorted((out / config).glob("*/*-*")):
            meta = json.loads((run_dir / "run.json").read_text())
            meta["summary"] = json.loads((run_dir / "summary.json").read_text())
            meta["samples"] = json.loads((run_dir / "samples.json").read_text())
            runs[meta["kind"], meta["script"], config].append(meta)
    return runs


def resources(run: dict[str, Any], args: dict[str, Any]) -> dict[str, float]:
    """CPU and connections during a latency run's measured phase."""
    start = run["samples"]["k6_started"]
    warmup, measure = seconds(args["warmup"]), seconds(args["measure"])
    lo, hi = start + warmup + EDGE_SECONDS, start + warmup + measure - EDGE_SECONDS
    cpu: dict[str, list[float]] = defaultdict(list)
    for sample in run["samples"]["cpu"]:
        if lo <= sample["t"] <= hi:
            cpu[sample["service"]].append(sample["cpu_percent"])
    pg = [s for s in run["samples"]["postgres"] if lo <= s["t"] <= hi]
    commits = 0.0
    if len(pg) >= 2:
        commits = (pg[-1]["commits"] - pg[0]["commits"]) / (pg[-1]["t"] - pg[0]["t"])
    busy = [s["active"] + s["in_transaction"] for s in pg]
    return {
        "api_cpu": statistics.fmean(cpu["api"]) if cpu["api"] else 0.0,
        "pg_cpu": statistics.fmean(cpu["postgres"]) if cpu["postgres"] else 0.0,
        "busy_mean": statistics.fmean(busy) if busy else 0.0,
        "busy_max": max(busy, default=0),
        "commits": commits,
    }


def latency_figures(run: dict[str, Any], args: dict[str, Any]) -> dict[str, float]:
    s = run["summary"]
    return {
        "rate": float(run["mode"]["RATE"]),
        # k6's rate for a tagged submetric divides by the whole test, warm-up included.
        "rps": metric(s, "http_reqs{phase:measure}", "count") / seconds(args["measure"]),
        "p50": metric(s, "http_req_duration{phase:measure}", "med"),
        "p95": metric(s, "http_req_duration{phase:measure}", "p(95)"),
        "p99": metric(s, "http_req_duration{phase:measure}", "p(99)"),
        "failed": metric(s, "http_req_failed{phase:measure}", "rate"),
        "cold_p50": metric(s, "http_req_duration{phase:warmup}", "med"),
        "cold_p95": metric(s, "http_req_duration{phase:warmup}", "p(95)"),
        "dropped": metric(s, "dropped_iterations", "count"),
        "aborts": metric(s, "unauthorized", "count") + metric(s, "rate_limited", "count"),
    } | resources(run, args)


def step_rows(run: dict[str, Any], args: dict[str, Any]) -> list[dict[str, float]]:
    return steps(
        run["summary"],
        script=run["script"],
        ramp_max=float(run["mode"]["RAMP_MAX"]),
        step_count=args["steps"],
        hold=args["hold"],
    )


def med(rows: list[dict[str, float]], key: str) -> float:
    return median([r[key] for r in rows])


def span(rows: list[dict[str, float]], key: str, fmt: str) -> str:
    low, high = min(r[key] for r in rows), max(r[key] for r in rows)
    return f"{format(low, fmt)} to {format(high, fmt)}"


def change(before: float, after: float) -> str:
    return f"{100 * (after - before) / before:+.0f}%" if before else "n/a"


def latency_section(runs: Runs, args: dict[str, Any], scripts: list[str]) -> list[str]:
    lines = [
        "| Script | Rate (iter/s) | Cache | Postgres CPU | API CPU | p50 ms | p95 ms | p99 ms "
        "| Req/s | Errors | Dropped | Busy conns (mean / max) | p95 range |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for script in scripts:
        figures = {
            c: [latency_figures(r, args) for r in runs["latency", script, c]] for c in CONFIGS
        }
        if not all(figures.values()):
            continue
        for config in CONFIGS:
            f = figures[config]
            lines.append(
                f"| {script} | {med(f, 'rate'):g} | {config.removeprefix('cache-')} | "
                f"{med(f, 'pg_cpu'):.0f}% | {med(f, 'api_cpu'):.0f}% | {med(f, 'p50'):.1f} | "
                f"{med(f, 'p95'):.1f} | {med(f, 'p99'):.1f} | {med(f, 'rps'):.1f} | "
                f"{100 * med(f, 'failed'):.2f}% | {med(f, 'dropped'):.0f} | "
                f"{med(f, 'busy_mean'):.1f} / {med(f, 'busy_max'):.0f} | {span(f, 'p95', '.1f')} |"
            )
        off, on = figures["cache-off"], figures["cache-on"]
        lines.append(
            f"| {script} | | change | {change(med(off, 'pg_cpu'), med(on, 'pg_cpu'))} | "
            f"{change(med(off, 'api_cpu'), med(on, 'api_cpu'))} | "
            f"{change(med(off, 'p50'), med(on, 'p50'))} | "
            f"{change(med(off, 'p95'), med(on, 'p95'))} | "
            f"{change(med(off, 'p99'), med(on, 'p99'))} | | | | | |"
        )
    return lines


def status_section(runs: Runs, scripts: list[str]) -> list[str]:
    """Responses by status code in each latency run's measured phase. Runs recorded
    before the scripts counted status codes show none."""
    lines = ["| Script | Cache | Run | Responses by status |", "|---|---|---|---|"]
    for script in scripts:
        for config in CONFIGS:
            for run in sorted(runs["latency", script, config], key=lambda r: r["repeat"]):
                counts = statuses(run["summary"], "phase:measure")
                shown = ", ".join(f"{code}: {n}" for code, n in counts.items()) or "not recorded"
                lines.append(
                    f"| {script} | {config.removeprefix('cache-')} | {run['repeat']} | {shown} |"
                )
    return lines


def cold_section(runs: Runs, args: dict[str, Any], scripts: list[str]) -> list[str]:
    lines = ["| Script | Cache | p50 ms | p95 ms |", "|---|---|---|---|"]
    for script in scripts:
        for config in CONFIGS:
            f = [latency_figures(r, args) for r in runs["latency", script, config]]
            if f:
                lines.append(
                    f"| {script} | {config.removeprefix('cache-')} | "
                    f"{med(f, 'cold_p50'):.1f} | {med(f, 'cold_p95'):.1f} |"
                )
    return lines


def capacity_section(runs: Runs, args: dict[str, Any], scripts: list[str]) -> list[str]:
    lines = [
        "| Script | Cache | Peak req/s | Peak range "
        f"| Sustained req/s (p95 <= {SLO_P95_MS:.0f} ms) | Sustained range | Runs not saturated |",
        "|---|---|---|---|---|---|---|",
    ]
    for script in scripts:
        for config in CONFIGS:
            found = runs["capacity", script, config]
            if not found:
                continue
            caps = [capacity(step_rows(r, args)) for r in found]
            lines.append(
                f"| {script} | {config.removeprefix('cache-')} | {med(caps, 'peak_rps'):.0f} | "
                f"{span(caps, 'peak_rps', '.0f')} | {med(caps, 'sustained_rps'):.0f} | "
                f"{span(caps, 'sustained_rps', '.0f')} | "
                f"{sum(bool(c['limit_not_reached']) for c in caps)} of {len(caps)} |"
            )
    return lines


def steps_section(runs: Runs, args: dict[str, Any], script: str) -> list[str]:
    per_config = {c: [step_rows(r, args) for r in runs["capacity", script, c]] for c in CONFIGS}
    if not all(per_config.values()):
        return []
    lines = [
        f"`{script}`, per step (medians):",
        "",
        "| Offered req/s | Achieved off | Achieved on | p95 off ms | p95 on ms | Errors off "
        "| Errors on |",
        "|---|---|---|---|---|---|---|",
    ]
    for index in range(args["steps"]):

        def at(config: str, key: str, i: int = index) -> float:
            return median([rows[i][key] for rows in per_config[config]])

        lines.append(
            f"| {at('cache-off', 'offered_rps'):.0f} | {at('cache-off', 'achieved_rps'):.0f} | "
            f"{at('cache-on', 'achieved_rps'):.0f} | {at('cache-off', 'p95'):.0f} | "
            f"{at('cache-on', 'p95'):.0f} | {100 * at('cache-off', 'failed'):.1f}% | "
            f"{100 * at('cache-on', 'failed'):.1f}% |"
        )
    return [*lines, ""]


def race_section(runs: Runs) -> list[str]:
    lines = [
        "| Cache | Run | Requests | 201 | 409 | Other errors | p50 ms | p95 ms "
        "| Winners (expected) | Overlapping active allocations | Bookings per GPU | Check |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for config in CONFIGS:
        for run in sorted(runs["race", "booking_race", config], key=lambda r: r["repeat"]):
            s, v = run["summary"], run["verify"]
            won = metric(s, "race_won", "count")
            per_gpu = sorted(set(v["race_bookings_per_gpu"].values()))
            ok = v["ok"] and won == v["race_winners"]
            lines.append(
                f"| {config.removeprefix('cache-')} | {run['repeat']} | "
                f"{metric(s, 'http_reqs{phase:measure}', 'count'):.0f} | {won:.0f} | "
                f"{metric(s, 'race_lost', 'count'):.0f} | "
                f"{100 * metric(s, 'http_req_failed{phase:measure}', 'rate'):.2f}% | "
                f"{metric(s, 'http_req_duration{phase:measure}', 'med'):.0f} | "
                f"{metric(s, 'http_req_duration{phase:measure}', 'p(95)'):.0f} | "
                f"{v['race_winners']} ({v['race_expected_winners']}) | "
                f"{v['overlapping_active_allocations']} | "
                f"{', '.join(map(str, per_gpu))} on {len(v['race_bookings_per_gpu'])} GPUs | "
                f"{'pass' if ok else 'FAIL'} |"
            )
    return lines


def hit_rate_section(out: Path) -> list[str]:
    paths = sorted((out / "hit-rate").glob("*/hit_rate.json"))
    if not paths:
        return []
    lines = [
        "| Script | Warm-up hits / misses | Warm-up rate | Measured hits / misses "
        "| Measured rate |",
        "|---|---|---|---|---|",
    ]

    def rate(x: dict[str, Any]) -> str:
        return f"{100 * x['hit_rate']:.1f}%" if x.get("hit_rate") is not None else "n/a"

    for path in paths:
        h = json.loads(path.read_text())
        w, m = h.get("warmup", {}), h["measure"]
        lines.append(
            f"| {path.parent.name} | {w.get('hits', 0)} / {w.get('misses', 0)} | {rate(w)} | "
            f"{m['hits']} / {m['misses']} | {rate(m)} |"
        )
    return lines


def report(out: Path) -> str:
    meta = json.loads((out / "meta.json").read_text())
    args = meta["args"]
    runs = load_runs(out)
    scripts = [
        s for s in ("availability", "slot_search", "browse_servers") if any(k[1] == s for k in runs)
    ]
    m = meta["machine"]
    lines = [
        f"Run `{out.name}`, commit `{m['commit'][:7]}`{' (dirty tree)' if m['dirty'] else ''}; "
        f"k6 {m['k6'].split()[1] if m['k6'] else '?'}; latency rates (iterations/s): "
        f"{meta.get('latency_rates')}"
        + (
            f", from the clean sustained rates in `{Path(meta['capacity_from']).name}`: "
            f"{meta.get('clean_sustained')}"
            if meta.get("capacity_from")
            else ""
        )
        + ".",
        "",
        "### Fixed rate, cache off vs on",
        "",
        *latency_section(runs, args, scripts),
        "",
        "Responses by status, measured phase:",
        "",
        *status_section(runs, scripts),
        "",
        "### Capacity",
        "",
        *capacity_section(runs, args, scripts),
        "",
    ]
    for script in scripts:
        lines += steps_section(runs, args, script)
    lines += [
        "### Warm-up at the fixed rate (cold cache)",
        "",
        *cold_section(runs, args, scripts),
        "",
        "### Booking race",
        "",
        *race_section(runs),
        "",
        "### Cache hit rate (separate untimed runs, per day entry)",
        "",
        *hit_rate_section(out),
        "",
    ]
    aborts = sum(
        metric(r["summary"], "unauthorized", "count")
        + metric(r["summary"], "rate_limited", "count")
        for found in runs.values()
        for r in found
    )
    lines.append(f"401 and 429 responses across all runs: {aborts:.0f} (any would abort a run).")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    text = report(target)
    (target / "report.md").write_text(text)
    sys.stdout.write(text)
