#!/usr/bin/env python3
"""Summarize a load-test output directory written by run.py, as Markdown.

    python3 loadtest/report.py loadtest/out/<run>

Each figure is the median over the repeats of one configuration; the range across
repeats is shown where it matters. Standard library only.
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from run import CONFIGS, seconds

# Samples this close to a phase boundary are dropped: k6 takes a moment to start, and
# docker stats averages over its own interval.
EDGE_SECONDS = 2


def load_runs(out: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    runs: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for config in CONFIGS:
        for run_dir in sorted((out / config).glob("*-*")):
            meta = json.loads((run_dir / "run.json").read_text())
            meta["summary"] = json.loads((run_dir / "summary.json").read_text())
            meta["samples"] = json.loads((run_dir / "samples.json").read_text())
            runs[meta["script"], config].append(meta)
    return runs


def metric(summary: dict[str, Any], name: str, stat: str) -> float:
    found = summary["metrics"].get(name)
    if found is None:
        return 0.0
    return float(found["values"].get(stat, 0.0))


def window(run: dict[str, Any], phase: str, args: dict[str, Any]) -> tuple[float, float]:
    start = run["samples"]["k6_started"]
    warmup, measure = seconds(args["warmup"]), seconds(args["measure"])
    if run["script"] == "booking_race":
        return start, start + 10**6
    if phase == "warmup":
        return start + EDGE_SECONDS, start + warmup - EDGE_SECONDS
    return start + warmup + EDGE_SECONDS, start + warmup + measure - EDGE_SECONDS


def resources(run: dict[str, Any], args: dict[str, Any]) -> dict[str, float]:
    lo, hi = window(run, "measure", args)
    cpu: dict[str, list[float]] = defaultdict(list)
    for sample in run["samples"]["cpu"]:
        if lo <= sample["t"] <= hi:
            cpu[sample["service"]].append(sample["cpu_percent"])
    pg = [s for s in run["samples"]["postgres"] if lo <= s["t"] <= hi]
    commits_per_second = 0.0
    if len(pg) >= 2:
        commits_per_second = (pg[-1]["commits"] - pg[0]["commits"]) / (pg[-1]["t"] - pg[0]["t"])
    return {
        "api_cpu": statistics.fmean(cpu["api"]) if cpu["api"] else 0.0,
        "pg_cpu": statistics.fmean(cpu["postgres"]) if cpu["postgres"] else 0.0,
        # Busy: running a statement or holding a transaction open, i.e. checked out of
        # the API's pool (5 connections plus 5 overflow).
        "pg_active_mean": statistics.fmean(s["active"] + s["in_transaction"] for s in pg)
        if pg
        else 0.0,
        "pg_active_max": max((s["active"] + s["in_transaction"] for s in pg), default=0),
        "pg_connections_max": max((s["connections"] for s in pg), default=0),
        "commits_per_second": commits_per_second,
    }


def figures(run: dict[str, Any], args: dict[str, Any]) -> dict[str, float]:
    s = run["summary"]
    values = {
        "rps": metric(s, "http_reqs{phase:measure}", "rate"),
        "requests": metric(s, "http_reqs{phase:measure}", "count"),
        "p50": metric(s, "http_req_duration{phase:measure}", "med"),
        "p95": metric(s, "http_req_duration{phase:measure}", "p(95)"),
        "p99": metric(s, "http_req_duration{phase:measure}", "p(99)"),
        "failed": metric(s, "http_req_failed{phase:measure}", "rate"),
        "cold_p50": metric(s, "http_req_duration{phase:warmup}", "med"),
        "cold_p95": metric(s, "http_req_duration{phase:warmup}", "p(95)"),
        "cold_rps": metric(s, "http_reqs{phase:warmup}", "rate"),
        "unauthorized": metric(s, "unauthorized", "count"),
        "rate_limited": metric(s, "rate_limited", "count"),
        "won": metric(s, "race_won", "count"),
        "lost": metric(s, "race_lost", "count"),
    }
    values |= resources(run, args)
    return values


def median(runs: list[dict[str, float]], key: str) -> float:
    return statistics.median(r[key] for r in runs)


def spread(runs: list[dict[str, float]], key: str, fmt: str) -> str:
    low, high = min(r[key] for r in runs), max(r[key] for r in runs)
    return f"{format(low, fmt)} to {format(high, fmt)}"


def report(out: Path) -> str:
    meta = json.loads((out / "meta.json").read_text())
    args = meta["args"]
    runs = load_runs(out)
    lines = [
        f"Run `{out.name}`, commit `{meta['machine']['commit'][:7]}`"
        f"{' (dirty tree)' if meta['machine']['dirty'] else ''}.",
        "",
    ]

    lines += [
        "| Script | Cache | Runs | Req/s | p50 ms | p95 ms | p99 ms | Errors "
        "| Req/s range | p95 range |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    by_key: dict[tuple[str, str], list[dict[str, float]]] = {
        key: [figures(r, args) for r in value] for key, value in runs.items()
    }
    timed = [k for k in sorted(by_key) if k[0] != "booking_race"]
    for script, config in timed:
        f = by_key[script, config]
        lines.append(
            f"| {script} | {config.removeprefix('cache-')} | {len(f)} | {median(f, 'rps'):.0f} | "
            f"{median(f, 'p50'):.1f} | {median(f, 'p95'):.1f} | {median(f, 'p99'):.1f} | "
            f"{100 * median(f, 'failed'):.2f}% | {spread(f, 'rps', '.0f')} | "
            f"{spread(f, 'p95', '.1f')} |"
        )

    lines += [
        "",
        "Warm-up (cold cache, first seconds of each run), medians:",
        "",
        "| Script | Cache | Req/s | p50 ms | p95 ms |",
        "|---|---|---|---|---|",
    ]
    for script, config in timed:
        f = by_key[script, config]
        lines.append(
            f"| {script} | {config.removeprefix('cache-')} | {median(f, 'cold_rps'):.0f} | "
            f"{median(f, 'cold_p50'):.1f} | {median(f, 'cold_p95'):.1f} |"
        )

    lines += [
        "",
        "Resources during the measured phase, medians (CPU in % of one core; busy "
        "connections are running a statement or inside a transaction, sampled 4 times a "
        "second):",
        "",
        "| Script | Cache | API CPU | Postgres CPU | PG busy conns (mean / max) "
        "| PG conns max | Commits/s |",
        "|---|---|---|---|---|---|---|",
    ]
    # Not the race: it lasts a second or two, shorter than docker's CPU sampling.
    for script, config in timed:
        f = by_key[script, config]
        lines.append(
            f"| {script} | {config.removeprefix('cache-')} | {median(f, 'api_cpu'):.0f}% | "
            f"{median(f, 'pg_cpu'):.0f}% | {median(f, 'pg_active_mean'):.1f} / "
            f"{median(f, 'pg_active_max'):.0f} | {median(f, 'pg_connections_max'):.0f} | "
            f"{median(f, 'commits_per_second'):.0f} |"
        )

    race_keys = [k for k in sorted(by_key) if k[0] == "booking_race"]
    if race_keys:
        lines += [
            "",
            "Booking race, every run:",
            "",
            "| Cache | Run | Requests | 201 | 409 | Other errors | p50 ms | p95 ms "
            "| Winners (expected) | Overlapping active allocations | Bookings per GPU | Check |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for script, config in race_keys:
            for run, f in zip(runs[script, config], by_key[script, config], strict=True):
                v = run["verify"]
                per_gpu = sorted(set(v["race_bookings_per_gpu"].values()))
                lines.append(
                    f"| {config.removeprefix('cache-')} | {run['repeat']} | {f['requests']:.0f} | "
                    f"{f['won']:.0f} | {f['lost']:.0f} | {100 * f['failed']:.2f}% | "
                    f"{f['p50']:.0f} | {f['p95']:.0f} | {v['race_winners']} "
                    f"({v['race_expected_winners']}) | {v['overlapping_active_allocations']} | "
                    f"{', '.join(map(str, per_gpu))} on {len(v['race_bookings_per_gpu'])} GPUs | "
                    f"{'pass' if v['ok'] and f['won'] == v['race_winners'] else 'FAIL'} |"
                )

    aborts = sum(f["unauthorized"] + f["rate_limited"] for fs in by_key.values() for f in fs)
    lines += ["", f"401 and 429 responses across all runs: {aborts:.0f} (any would abort a run)."]

    hit_rates = sorted((out / "hit-rate").glob("*/hit_rate.json"))
    if hit_rates:
        lines += [
            "",
            "Cache hit rate (separate untimed runs with counting on; per day entry):",
            "",
            "| Script | Warm-up hits / misses | Warm-up rate | Measured hits / misses "
            "| Measured rate |",
            "|---|---|---|---|---|",
        ]
        for path in hit_rates:
            h = json.loads(path.read_text())
            w, m = h.get("warmup", {}), h["measure"]

            def rate(x: dict[str, Any]) -> str:
                return f"{100 * x['hit_rate']:.1f}%" if x.get("hit_rate") is not None else "n/a"

            lines.append(
                f"| {path.parent.name} | {w.get('hits', 0)} / {w.get('misses', 0)} | {rate(w)} | "
                f"{m['hits']} / {m['misses']} | {rate(m)} |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    target = Path(sys.argv[1])
    text = report(target)
    (target / "report.md").write_text(text)
    sys.stdout.write(text)
