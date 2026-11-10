"""evals/agent/cases.md: the dataset as one table row per case, for review.

Generated from cases.jsonl. The "Expected" column is the case's own summary; "Checks"
is rendered from the structured expectations, so a summary that drifts from what is
actually checked shows up side by side.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.agent.evaluation.cases import EVAL_NOW, Case, Expectation, ExpectedCall


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _time(value: datetime | None) -> str:
    return "" if value is None else value.strftime("%a %d %H:%M")


def _value(value: Any) -> str:
    if isinstance(value, dict) and len(value) == 1:
        ((op, operand),) = value.items()
        return f"{op.replace('_', ' ')} {_value(operand)}"
    if isinstance(value, str):
        try:
            return _time(datetime.fromisoformat(value))
        except ValueError:
            return value
    return str(value)


def _call(call: ExpectedCall) -> str:
    args = ", ".join(f"{k}={_value(v)}" for k, v in call.args.items())
    turn = f" (turn {call.turn + 1})" if call.turn else ""
    return f"{call.name}({args}){turn}"


def _checks(path: Expectation) -> str:
    parts: list[str] = []
    if path.calls:
        parts.append("calls " + "; ".join(_call(c) for c in path.calls))
    if path.forbidden:
        parts.append("never " + ", ".join(path.forbidden))
    if path.cards:
        cards = []
        for card in path.cards:
            what = "book" if card.action == "create_reservation" else "cancel"
            detail = " ".join(
                part
                for part in (
                    f"{card.gpus}x" if card.gpus else "",
                    card.server or "",
                    card.reservation or "",
                    f"{_time(card.start)}-{card.end:%H:%M}" if card.start and card.end else "",
                    card.status_after or card.effect or "",
                )
                if part
            )
            cards.append(f"{what} {detail} → {card.decision.value}".strip())
        parts.append("cards: " + "; ".join(cards))
    else:
        parts.append("no card")
    if path.errors:
        parts.append("errors " + ", ".join(path.errors))
    created = [f"+{r.gpu_count}x {r.server} {r.status}" for r in path.db.created]
    changed = [f"{r.reservation!r} → {r.status}" for r in path.db.changed]
    parts.append("db " + (", ".join(created + changed) if created or changed else "unchanged"))
    if path.actions:
        parts.append("actions " + ", ".join(path.actions))
    if path.citations:
        parts.append("cites " + " or ".join(path.citations))
    if path.question_terms:
        parts.append("asks about " + " / ".join(path.question_terms))
    if path.mentions:
        parts.append("mentions " + " / ".join(path.mentions))
    if path.must_not_mention:
        parts.append("never says " + " / ".join(path.must_not_mention))
    return "; ".join(parts)


def render(cases: Sequence[Case]) -> str:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category.value] = counts.get(case.category.value, 0) + 1
    judged = sum(case.judge is not None for case in cases)
    lines = [
        "# Agent evaluation cases",
        "",
        "Generated from `cases.jsonl` by `python -m app.agent.evaluation describe`; do not "
        "edit by hand.",
        "",
        f"Every case runs at {EVAL_NOW:%A %Y-%m-%d %H:%M} UTC: 10:00 in New York (NLP lab), "
        "09:00 in Chicago (Robotics lab), 07:00 in Los Angeles (Vision lab). Peak time is "
        "09:00-18:00 on weekdays in the lab's zone. Times below are in the lab's zone.",
        "",
        f"{len(cases)} cases, {judged} also judged: "
        + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())),
        "",
        "| Id | Category | User says | Expected | Checks |",
        "|---|---|---|---|---|",
    ]
    for case in cases:
        said = " / ".join(f"“{turn.say}”" for turn in case.turns)
        outcomes = case.expect
        checks = (
            _checks(outcomes[0])
            if len(outcomes) == 1
            else " **or** ".join(f"({i}) {_checks(p)}" for i, p in enumerate(outcomes, start=1))
        )
        if case.judge is not None:
            checks += "; judged: " + ", ".join(case.judge.criteria)
        lines.append(
            f"| {case.id} | {case.category.value} | {_cell(said)} | {_cell(case.outcome)} "
            f"| {_cell(checks)} |"
        )
    return "\n".join(lines) + "\n"
