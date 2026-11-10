"""The Markdown label sheet: blind, mechanical support notes, verdicts read back."""

import json
from pathlib import Path

import pytest

from app.agent.evaluation.compare import load_run
from app.agent.evaluation.labels import (
    Answer,
    apply_verdicts,
    collect,
    read_verdicts,
    render,
    statements,
    support,
)
from app.agent.evaluation.record import HAND_LABELS_PATH, RUNS_DIR

SECTIONS = [
    {
        "ref": 1,
        "document": "fair-use-and-quota",
        "heading": "Your weekly quota > Default allowance",
        "content": "Every new account starts with a quota of 48 GPU-hours per week.",
    },
    {
        "ref": 2,
        "document": "peak-hours",
        "heading": "The per-booking peak limit",
        "content": "A single booking may use at most 4 GPU-hours inside peak time.",
    },
]


def test_statements_skip_questions_and_fragments() -> None:
    answer = "You get 48 GPU-hours a week [1].\n\n- Peak limit applies.\nWant more detail?"
    assert statements(answer) == ["You get 48 GPU-hours a week [1].", "Peak limit applies."]


def test_support_shows_citations_overlap_or_nothing() -> None:
    assert support("You get 48 GPU-hours a week [1].", SECTIONS) == (
        "cites [1] fair-use-and-quota > Your weekly quota > Default allowance"
    )
    assert "not among the retrieved" in support("It is fine [7].", SECTIONS)
    assert support("A booking may use at most 4 GPU-hours in peak time.", SECTIONS).startswith(
        "not cited; closest section [2] peak-hours"
    )
    assert support("Unused hours roll over to next month.", SECTIONS) == (
        "not cited; no support found"
    )


def answer(verdicts: dict[str, str | None]) -> Answer:
    return Answer(
        "a1b2c3d4e5f6",
        "policy-quota-01",
        "How many GPU-hours do I get?",
        "You get 48 GPU-hours a week [1].",
        ["48 GPU-hours a week."],
        SECTIONS,
        list(verdicts),
        verdicts,
    )


def test_verdicts_round_trip_through_markdown() -> None:
    blank = render([answer({"grounded": None, "answers": None})])
    assert "- verdict grounded:\n" in blank
    filled = blank.replace("- verdict grounded:", "- verdict grounded: pass").replace(
        "- verdict answers:", "- verdict answers: FAIL"
    )
    assert read_verdicts(filled) == {
        ("a1b2c3d4e5f6", "grounded"): "pass",
        ("a1b2c3d4e5f6", "answers"): "fail",
    }
    with pytest.raises(ValueError, match="not pass or fail"):
        read_verdicts(blank.replace("- verdict grounded:", "- verdict grounded: maybe"))


def test_missing_verdicts_are_listed() -> None:
    rows = [
        {"answer_id": "a1b2c3d4e5f6", "case_id": "x", "criterion": "grounded", "verdict": None},
        {"answer_id": "a1b2c3d4e5f6", "case_id": "x", "criterion": "answers", "verdict": None},
    ]
    updated, missing = apply_verdicts(rows, {("a1b2c3d4e5f6", "grounded"): "pass"})
    assert [r["verdict"] for r in updated] == ["pass", None]
    assert missing == ["a1b2c3d4e5f6 answers (x)"]


def test_the_committed_sheet_renders_blind_with_every_verdict() -> None:
    rows = [json.loads(line) for line in HAND_LABELS_PATH.read_text().splitlines() if line]
    runs = [
        load_run(d)
        for d in sorted((RUNS_DIR / "pilot").iterdir())
        if "gpt" in d.name or "haiku" in d.name
    ]
    text = render(collect(rows, runs))
    assert text.count("- verdict ") == len(rows) == 60
    lowered = text.lower()
    for name in ("gpt", "haiku", "claude", "qwen", "openai", "anthropic"):
        assert name not in lowered
    assert len(read_verdicts(text)) == 60


def test_an_answer_from_no_given_run_is_an_error(tmp_path: Path) -> None:
    rows = [{"answer_id": "ffffffffffff", "case_id": "x", "criterion": "grounded"}]
    with pytest.raises(ValueError, match="in no run given"):
        collect(rows, [])
