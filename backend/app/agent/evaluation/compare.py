"""The comparison report across configurations.

A configuration is a model at one reasoning setting; each has its runs (normally two).
A judged case passes a run only if its deterministic checks pass and the primary judge
passes every criterion. Across a configuration's runs a case passes only if it passed
every run (strict), and the pairwise tests use those strict outcomes on the cases that
every configuration scored (see pairing.py). The report states how many cases that
left, what the case count can detect after the Holm correction, and how far the two
judges and the hand labels agree.
"""

import hashlib
import itertools
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from app.agent.evaluation.cases import Case
from app.agent.evaluation.judge import PRIMARY_JUDGE, SECOND_JUDGE, Judgment, read_judgments
from app.agent.evaluation.pairing import (
    EXCLUDED_STATUSES,
    common_cases,
    holm,
    mcnemar_exact,
    pair,
    percentile,
    smallest_significant_splits,
    strict_outcomes,
)
from app.agent.evaluation.record import read_cases
from app.llm.names import normalize_model_name

ALPHA: Final = 0.05
MILLION: Final = 1_000_000
SECONDS_PER_HOUR: Final = 3600


class CompareError(ValueError):
    pass


@dataclass
class Run:
    directory: Path
    config: dict[str, Any]
    rows: list[dict[str, Any]]
    judgments: list[Judgment]

    @property
    def label(self) -> str:
        return f"{self.config['model']} ({self.config['reasoning']})"


def answer_id(run_id: str, case_id: str) -> str:
    """An opaque name for one answer, so hand labels can be made without seeing which
    model wrote it."""
    return hashlib.sha256(f"{run_id}/{case_id}".encode()).hexdigest()[:12]


def load_run(directory: Path) -> Run:
    config = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    return Run(directory, config, read_cases(directory), read_judgments(directory))


def scored_rows(run: Run, cases: dict[str, Case]) -> list[dict[str, Any]]:
    """The run's rows with `passed` including the primary judge's verdict."""
    primary = {j.case_id: j for j in run.judgments if j.judge == PRIMARY_JUDGE}
    rows = []
    for row in run.rows:
        case = cases.get(row["id"])
        passed = bool(row["passed"])
        if case is not None and case.judge is not None and row["status"] == "ok":
            judgment = primary.get(row["id"])
            if judgment is None or judgment.passed is None:
                raise CompareError(
                    f"{run.directory.name}: {row['id']} has no usable verdict from "
                    f"{PRIMARY_JUDGE}; run `judge` on this run first"
                )
            passed = passed and judgment.passed
        rows.append({**row, "passed": passed})
    return rows


# --- cost ---


def call_cost(call: dict[str, Any], prices: dict[str, Any]) -> float | None:
    model = call.get("model")
    rates = prices["models"].get(normalize_model_name(str(model))) if model else None
    if rates is None:
        return None
    cached = call.get("cache_read_tokens", 0)
    written = call.get("cache_write_tokens", 0)
    uncached = max(call.get("input_tokens", 0) - cached - written, 0)
    return (
        float(
            uncached * rates["input"]
            + cached * rates["cache_read"]
            + written * rates["cache_write"]
            + call.get("output_tokens", 0) * rates["output"]
        )
        / MILLION
    )


def run_cost(run: Run, prices: dict[str, Any]) -> tuple[float | None, str]:
    """Dollars for the run's model calls, and how they were priced."""
    if run.config["provider"] == "vllm":
        gpu = run.config.get("serving", {}).get("gpu", "")
        rate = prices["gpu_per_hour"].get(gpu)
        if rate is None:
            return None, f"no price for GPU {gpu!r}"
        started = datetime.fromisoformat(run.config["started_at"])
        finished = datetime.fromisoformat(run.config["finished_at"])
        hours = (finished - started).total_seconds() / SECONDS_PER_HOUR
        return hours * rate, f"{gpu} at ${rate}/h for {hours * 60:.0f} min"
    costs = [call_cost(c, prices) for row in run.rows for c in row.get("model_calls", [])]
    if any(cost is None for cost in costs):
        return None, "a model with no price"
    return sum(c for c in costs if c is not None), "tokens at list prices"


def tokens(run: Run) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for row in run.rows:
        for call in row.get("model_calls", []):
            for key in ("input_tokens", "cache_read_tokens", "output_tokens", "reasoning_tokens"):
                totals[key] += int(call.get(key, 0))
    return dict(totals)


# --- the report ---


def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total} ({100 * passed / total:.0f}%)" if total else "-"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _p(value: float) -> str:
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def report(
    runs: Sequence[Run],
    cases: dict[str, Case],
    prices: dict[str, Any],
    hand_labels: Sequence[dict[str, Any]] = (),
) -> str:
    by_config: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_config[run.label].append(run)
    tags = {run.config.get("tag") for run in runs}
    if len(tags) > 1:
        raise CompareError(f"the runs mix tags {sorted(map(str, tags))}; compare like with like")
    datasets = {run.config["dataset_sha256"] for run in runs}
    if len(datasets) > 1:
        raise CompareError("the runs used different versions of the dataset")
    rubrics = {j.rubric_sha256 for run in runs for j in run.judgments}
    if len(rubrics) > 1:
        raise CompareError("the runs were judged with different rubrics")

    scored = {label: [scored_rows(r, cases) for r in group] for label, group in by_config.items()}
    outcomes = {label: strict_outcomes(rows) for label, rows in scored.items()}
    common = common_cases(outcomes)
    labels = sorted(by_config)
    lines = ["# Agent evaluation comparison", ""]

    lines += [
        "## Configurations",
        "",
        "| Configuration | Runs | Reported model | Commit | Rubric |",
    ]
    lines += ["|---|---|---|---|---|"]
    for label in labels:
        group = by_config[label]
        reported = sorted({m for r in group for m in r.config.get("reported_models", [])})
        commits = sorted({r.config["git_commit"][:7] for r in group})
        rubric = sorted({j.rubric_version for r in group for j in r.judgments}) or ["-"]
        lines.append(
            f"| {label} | {len(group)} | {', '.join(reported) or '-'} | {', '.join(commits)} "
            f"| {', '.join(rubric)} |"
        )

    lines += ["", "## Cases compared", ""]
    lines.append(
        f"{len(common.cases)} cases have an outcome in every run of every configuration; "
        f"{len(common.dropped)} were dropped from every comparison."
    )
    lines += ["", "| Configuration | Run | Infrastructure (left out) | Aborted (left out) |"]
    lines += ["|---|---|---|---|"]
    for label in labels:
        for run, rows in zip(by_config[label], scored[label], strict=True):
            counts = Counter(r["status"] for r in rows if r["status"] in EXCLUDED_STATUSES)
            lines.append(
                f"| {label} | {run.directory.name} | {counts['infra']} | {counts['aborted']} |"
            )
    if common.dropped:
        lines += [""] + [
            f"- {case_id}: left out in {', '.join(where)}"
            for case_id, where in sorted(common.dropped.items())
        ]

    lines += ["", "## Pass rates", ""]
    lines += [
        "Strict: passed in every run. Per run: each run on its own, on the same cases.",
        "",
        "| Configuration | Strict | Per run | Runs disagree | Wrong cards (per run) |",
        "|---|---|---|---|---|",
    ]
    for label in labels:
        strict = sum(bool(outcomes[label][c]) for c in common.cases)
        per_run = []
        for rows in scored[label]:
            passed = {r["id"]: r["passed"] for r in rows}
            per_run.append(sum(bool(passed.get(c)) for c in common.cases))
        agree = [
            c
            for c in common.cases
            if len({next(r["passed"] for r in rows if r["id"] == c) for rows in scored[label]}) > 1
        ]
        wrong = [sum(r.get("wrong_cards", 0) for r in rows) for rows in scored[label]]
        lines.append(
            f"| {label} | {_rate(strict, len(common.cases))} | "
            f"{', '.join(_rate(p, len(common.cases)) for p in per_run)} | {len(agree)} | "
            f"{', '.join(map(str, wrong))} |"
        )

    categories = sorted({cases[c].category.value for c in common.cases if c in cases})
    lines += ["", "## By category (strict)", ""]
    lines += ["| Category | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for category in categories:
        ids = [c for c in common.cases if c in cases and cases[c].category.value == category]
        cells = [_rate(sum(bool(outcomes[lb][c]) for c in ids), len(ids)) for lb in labels]
        lines.append(f"| {category} | " + " | ".join(cells) + " |")

    pairs = list(itertools.combinations(labels, 2))
    lines += ["", "## Paired comparison", ""]
    if pairs:
        tests = [pair(outcomes[a], outcomes[b], common.cases) for a, b in pairs]
        raw = [mcnemar_exact(t.only_a, t.only_b) for t in tests]
        adjusted = holm(raw)
        lines += [
            "Exact McNemar test on the strict outcomes, Holm-corrected over "
            f"{len(pairs)} comparisons.",
            "",
            "| A | B | Both pass | Only A | Only B | Neither | p | Holm p |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for (a, b), t, p, q in zip(pairs, tests, raw, adjusted, strict=True):
            lines.append(
                f"| {a} | {b} | {t.both} | {t.only_a} | {t.only_b} | {t.neither} "
                f"| {_p(p)} | {_p(q)} |"
            )
        splits = smallest_significant_splits(ALPHA, len(pairs), 20)
        shown = ", ".join(f"{x}-{y}" for x, y in splits[:6])
        lines += [
            "",
            f"Power: with {len(common.cases)} cases, only the cases where two configurations "
            "disagree carry information. For the most significant of "
            f"{len(pairs)} comparisons to clear Holm's threshold ({ALPHA}/{len(pairs)}), the "
            f"disagreements must split at least {shown}. A difference of a few cases cannot be "
            "told apart from noise, and a pair that is not significant is not shown to be equal.",
        ]

    lines += ["", "## Latency", ""]
    lines += [
        "Seconds. A turn runs from the user's message or click to the answer or the next "
        "card. The warm-up call before each run, which absorbs a cold start, is not counted.",
        "",
        "| Configuration | Turn p50 | Turn p95 | Model call p50 | Model call p95 | Warm-up |",
        "|---|---|---|---|---|---|",
    ]
    for label in labels:
        rows = [r for group in scored[label] for r in group if r["status"] == "ok"]
        turns = [s["seconds"] for r in rows for s in r.get("segments", [])]
        calls = [
            c["seconds"] for r in rows for c in r.get("model_calls", []) if not c.get("failure")
        ]
        warm = [r.config.get("cold_start_seconds") for r in by_config[label]]
        warm_text = ", ".join(_seconds(w) for w in warm if w is not None) or "-"
        lines.append(
            f"| {label} | {_seconds(percentile(turns, 0.5))} | {_seconds(percentile(turns, 0.95))} "
            f"| {_seconds(percentile(calls, 0.5))} | {_seconds(percentile(calls, 0.95))} "
            f"| {warm_text} |"
        )

    lines += ["", "## Tokens and cost", ""]
    lines += [
        "| Configuration | Run | Input | Cache read | Output (reasoning) | Cost | Priced as |",
        "|---|---|---|---|---|---|---|",
    ]
    for label in labels:
        for run in by_config[label]:
            used = tokens(run)
            cost, how = run_cost(run, prices)
            cost_text = "-" if cost is None else f"${cost:.2f}"
            lines.append(
                f"| {label} | {run.directory.name} | {used.get('input_tokens', 0):,} "
                f"| {used.get('cache_read_tokens', 0):,} | {used.get('output_tokens', 0):,} "
                f"({used.get('reasoning_tokens', 0):,}) | {cost_text} | {how} |"
            )

    lines += ["", "## Judges", ""]
    lines += judge_agreement(by_config, hand_labels)
    return "\n".join(lines) + "\n"


def judge_agreement(
    by_config: dict[str, list[Run]], hand_labels: Sequence[dict[str, Any]]
) -> list[str]:
    """Agreement between the two judges per answering model, and of each judge with
    the hand labels, counted per criterion verdict."""
    labels_by_key = {
        (h["answer_id"], h["criterion"]): h["verdict"]
        for h in hand_labels
        if h.get("verdict") in ("pass", "fail")
    }
    lines = [
        f"Primary judge: {PRIMARY_JUDGE}; second judge: {SECOND_JUDGE}. Agreement counts "
        "criterion verdicts.",
        "",
        "| Answers by | Judges agree | Primary vs hand labels | Second vs hand labels |",
        "|---|---|---|---|",
    ]
    for label in sorted(by_config):
        agree = total = 0
        vs_hand: dict[str, list[int]] = {PRIMARY_JUDGE: [0, 0], SECOND_JUDGE: [0, 0]}
        for run in by_config[label]:
            run_id = run.config["run_id"]
            by_case: dict[str, dict[str, Judgment]] = defaultdict(dict)
            for j in run.judgments:
                by_case[j.case_id][j.judge] = j
            for case_id, judges in by_case.items():
                first, second = judges.get(PRIMARY_JUDGE), judges.get(SECOND_JUDGE)
                if first and second and first.verdicts and second.verdicts:
                    for criterion, verdict in first.verdicts.items():
                        other = second.verdicts.get(criterion)
                        if other is not None:
                            total += 1
                            agree += verdict["verdict"] == other["verdict"]
                for name, judgment in judges.items():
                    for criterion, verdict in judgment.verdicts.items():
                        truth = labels_by_key.get((answer_id(run_id, case_id), criterion))
                        if truth is not None and name in vs_hand:
                            vs_hand[name][0] += verdict["verdict"] == truth
                            vs_hand[name][1] += 1
        lines.append(
            f"| {label} | {_rate(agree, total)} | {_rate(*vs_hand[PRIMARY_JUDGE])} "
            f"| {_rate(*vs_hand[SECOND_JUDGE])} |"
        )
    return lines


def label_sheet(runs: Sequence[Run], cases: dict[str, Case], limit: int) -> list[dict[str, Any]]:
    """Rows to label by hand: judged answers taken in turn from each configuration, so
    every answering model is represented, under an opaque answer id rather than the run
    (whose name says the model). `verdict` is left empty to be filled in."""
    queues: dict[str, list[tuple[Run, dict[str, Any]]]] = defaultdict(list)
    for run in runs:
        for row in run.rows:
            case = cases.get(row["id"])
            if case is not None and case.judge is not None and row["status"] == "ok":
                queues[run.label].append((run, row))
    picked: list[dict[str, Any]] = []
    for group in itertools.zip_longest(*(queues[k] for k in sorted(queues))):
        for item in group:
            if item is None or len(picked) >= limit * 3:
                continue
            run, row = item
            case = cases[row["id"]]
            assert case.judge is not None  # noqa: S101 - filtered above
            for criterion in case.judge.criteria:
                picked.append(
                    {
                        "answer_id": answer_id(run.config["run_id"], row["id"]),
                        "case_id": row["id"],
                        "criterion": criterion,
                        "question": " / ".join(t.say for t in case.turns),
                        "answer": (row.get("answers") or [""])[-1],
                        "reference": case.judge.reference,
                        "retrieved": [
                            f"[{h.get('ref')}] {h.get('document')} > {h.get('heading')}"
                            for h in row.get("retrieved", [])
                        ],
                        "verdict": None,
                    }
                )
    answers = set(list(dict.fromkeys(p["answer_id"] for p in picked))[:limit])
    return [p for p in picked if p["answer_id"] in answers]
