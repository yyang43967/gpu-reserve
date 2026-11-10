"""Judges, statistics, and the comparison report, with scripted models and synthetic
runs: no API is called."""

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from app.agent.evaluation.cases import Case, load_cases
from app.agent.evaluation.compare import (
    CompareError,
    Run,
    answer_id,
    call_cost,
    label_sheet,
    report,
)
from app.agent.evaluation.judge import (
    PRIMARY_JUDGE,
    SECOND_JUDGE,
    JudgeCache,
    Judgment,
    Rubric,
    judge_input,
    judge_one,
    judge_rows,
    parse_reply,
)
from app.agent.evaluation.pairing import (
    holm,
    mcnemar_exact,
    percentile,
    smallest_significant_splits,
)
from app.agent.evaluation.record import CASES_PATH, PRICES_PATH, RUBRIC_PATH
from app.llm.scripted import ScriptedChatModel

# --- statistics ---


def test_exact_mcnemar() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(9, 1) == pytest.approx(0.021484375)


def test_holm_adjusts_in_input_order_and_stays_monotone() -> None:
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5]) == [0.5]


def test_power_splits_follow_the_holm_threshold() -> None:
    splits = smallest_significant_splits(0.05, 6, 12)
    # 0.05 / 6 = 0.0083: 7-0 gives 0.0156, 8-0 gives 0.0078.
    assert splits[0] == (8, 0)
    assert all(mcnemar_exact(a, b) < 0.05 / 6 for a, b in splits)


def test_percentile_interpolates() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)


# --- the rubric and the dataset ---


def test_the_rubric_has_a_version_and_covers_every_criterion_in_use() -> None:
    rubric = Rubric.load(RUBRIC_PATH)
    assert rubric.version == "1"
    assert len(rubric.sha256) == 64
    used = {c for case in load_cases(CASES_PATH) if case.judge for c in case.judge.criteria}
    assert used == {"grounded", "answers", "declines"}
    for criterion in used:
        assert f"## {criterion}" in rubric.text


def test_judged_cases() -> None:
    judged = [case for case in load_cases(CASES_PATH) if case.judge is not None]
    assert len(judged) == 17
    assert all(case.judge and case.judge.reference for case in judged)


# --- one judge ---

POLICY = Case.model_validate(
    {
        "id": "policy-x-01",
        "category": "policy",
        "user": "ada.lovelace@example.edu",
        "outcome": "Answers from the quota policy.",
        "turns": [{"say": "How many GPU-hours do I get?"}],
        "expect": {"calls": [{"name": "search_policies"}]},
        "judge": {"criteria": ["grounded", "answers"], "reference": ["48 GPU-hours a week."]},
    }
)
ROW: dict[str, Any] = {
    "id": "policy-x-01",
    "status": "ok",
    "passed": True,
    "answers": ["You get 48 GPU-hours a week [1]."],
    "retrieved": [
        {
            "ref": 1,
            "document": "fair-use-and-quota",
            "heading": "Default",
            "content": "48 GPU-hours.",
        }
    ],
}
GOOD = json.dumps(
    {
        "verdicts": {
            "grounded": {"verdict": "pass", "reason": "Section 1 says 48."},
            "answers": {"verdict": "pass", "reason": "States the fact."},
        }
    }
)


def scripted(*replies: str) -> ScriptedChatModel:
    queue = list(replies)

    def respond(messages: list[BaseMessage]) -> AIMessage:
        return AIMessage(queue.pop(0))

    return ScriptedChatModel(responder=respond)


def test_the_judge_sees_the_answer_but_not_who_wrote_it() -> None:
    text = judge_input(POLICY, {**ROW, "model": "gpt-5.4-mini"})
    assert "You get 48 GPU-hours a week [1]." in text
    assert "[1] fair-use-and-quota > Default" in text
    assert "48 GPU-hours a week." in text
    assert "gpt" not in text


def test_parse_reply_needs_every_criterion() -> None:
    assert parse_reply(f"Here: {GOOD}", ["grounded", "answers"])["answers"]["verdict"] == "pass"
    with pytest.raises(ValueError, match="no verdict"):
        parse_reply(GOOD, ["grounded", "declines"])
    with pytest.raises(ValueError, match="no JSON"):
        parse_reply("pass", ["grounded"])


async def test_an_unparsable_reply_is_asked_for_once_more() -> None:
    rubric = Rubric.load(RUBRIC_PATH)
    judgment = await judge_one(scripted("Looks fine.", GOOD), "j", rubric, POLICY, ROW)
    assert judgment.passed is True
    failed = await judge_one(scripted("no", "still no"), "j", rubric, POLICY, ROW)
    assert failed.passed is None
    assert failed.error is not None


async def test_two_verdicts_per_judged_answer_and_a_cache(tmp_path: Path) -> None:
    rubric = Rubric.load(RUBRIC_PATH)
    cache = JudgeCache(tmp_path / "cache.jsonl")
    models = {PRIMARY_JUDGE: scripted(GOOD), SECOND_JUDGE: scripted(GOOD)}
    skipped = {**ROW, "id": "policy-x-01", "status": "timeout"}
    judgments = await judge_rows({"policy-x-01": POLICY}, [ROW, skipped], models, rubric, cache)
    assert sorted(j.judge for j in judgments) == sorted([PRIMARY_JUDGE, SECOND_JUDGE])
    cache.save()
    again = await judge_rows(
        {"policy-x-01": POLICY},
        [ROW],
        {PRIMARY_JUDGE: scripted(), SECOND_JUDGE: scripted()},
        rubric,
        JudgeCache(tmp_path / "cache.jsonl"),
    )
    assert all(j.cached and j.passed for j in again)


# --- the comparison ---

CASES = {"policy-x-01": POLICY}
for i in range(1, 4):
    data = {
        "id": f"book-x-0{i}",
        "category": "booking",
        "user": "ada.lovelace@example.edu",
        "outcome": "Books.",
        "turns": [{"say": "Book"}],
        "expect": {},
    }
    CASES[data["id"]] = Case.model_validate(data)


def run(
    model: str,
    repeat: int,
    passed: dict[str, bool],
    *,
    status: dict[str, str] | None = None,
    judged: bool | None = True,
) -> Run:
    rows = [
        {
            "id": case_id,
            "status": (status or {}).get(case_id, "ok"),
            "passed": ok,
            "wrong_cards": 0,
            "answers": ["ok"],
            "segments": [{"seconds": 2.0}],
            "model_calls": [
                {
                    "seconds": 1.0,
                    "model": model,
                    "input_tokens": 3000,
                    "cache_read_tokens": 2000,
                    "cache_write_tokens": 0,
                    "output_tokens": 100,
                    "reasoning_tokens": 40,
                }
            ],
        }
        for case_id, ok in passed.items()
    ]
    judgments = []
    if judged is not None:
        for name in (PRIMARY_JUDGE, SECOND_JUDGE):
            verdict = "pass" if judged else "fail"
            judgments.append(
                Judgment(
                    "policy-x-01",
                    name,
                    "1",
                    "abc",
                    verdicts={
                        "grounded": {"verdict": verdict, "reason": ""},
                        "answers": {"verdict": verdict, "reason": ""},
                    },
                )
            )
    config = {
        "run_id": f"{model}-r{repeat}",
        "model": model,
        "reasoning": "effort=low",
        "provider": "openai",
        "reported_models": [model],
        "git_commit": "abcdef1234",
        "dataset_sha256": "d",
        "cold_start_seconds": 1.5 if repeat == 1 else None,
        "started_at": "2026-11-10T00:00:00+00:00",
        "finished_at": "2026-11-10T00:10:00+00:00",
    }
    return Run(Path(f"{model}-r{repeat}"), config, rows, judgments)


PRICES = json.loads(PRICES_PATH.read_text())
ALL = {"policy-x-01": True, "book-x-01": True, "book-x-02": True, "book-x-03": True}


def test_a_judged_case_needs_the_primary_judges_pass() -> None:
    text = report(
        [run("gpt-5.4-mini", 1, ALL), run("gpt-5.4-mini", 2, ALL, judged=False)], CASES, PRICES
    )
    # The policy case fails run 2 on the judge, so it is not a strict pass.
    assert "| gpt-5.4-mini (effort=low) | 3/4 (75%) | 4/4 (100%), 3/4 (75%) | 1 |" in text


def test_missing_verdicts_stop_the_report() -> None:
    with pytest.raises(CompareError, match="run `judge`"):
        report([run("gpt-5.4-mini", 1, ALL, judged=None)], CASES, PRICES)


def test_a_case_left_out_anywhere_is_dropped_and_counted() -> None:
    runs = [
        run("gpt-5.4-mini", 1, ALL),
        run("gpt-5.4-mini", 2, ALL),
        run("claude-haiku-5-5", 1, ALL, status={"book-x-03": "infra"}),
        run("claude-haiku-5-5", 2, {**ALL, "book-x-02": False}),
    ]
    text = report(runs, CASES, PRICES)
    assert "3 cases have an outcome in every run of every configuration; 1 were dropped" in text
    assert "- book-x-03: left out in claude-haiku-5-5 (effort=low)" in text
    assert "| claude-haiku-5-5 (effort=low) | claude-haiku-5-5-r1 | 1 | 0 |" in text
    assert "| claude-haiku-5-5 (effort=low) | gpt-5.4-mini (effort=low) | 2 | 0 | 1 | 0 |" in text
    assert "Holm-corrected over 1 comparisons" in text


def test_cost_counts_uncached_input_cache_reads_and_output() -> None:
    call = {
        "model": "gpt-5.4-mini-2026-03-17",
        "input_tokens": 3000,
        "cache_read_tokens": 2000,
        "cache_write_tokens": 0,
        "output_tokens": 100,
    }
    # 1,000 uncached at $0.75, 2,000 cached at $0.075, 100 out at $4.50, per million.
    assert call_cost(call, PRICES) == pytest.approx((750 + 150 + 450) / 1_000_000)
    assert call_cost({**call, "model": "unknown"}, PRICES) is None


def test_the_label_sheet_takes_answers_from_every_configuration() -> None:
    runs = [run("gpt-5.4-mini", 1, ALL), run("claude-haiku-5-5", 1, ALL)]
    rows = label_sheet(runs, CASES, limit=2)
    assert {r["answer_id"] for r in rows} == {
        answer_id("gpt-5.4-mini-r1", "policy-x-01"),
        answer_id("claude-haiku-5-5-r1", "policy-x-01"),
    }
    assert not any("gpt" in json.dumps(r) or "haiku" in json.dumps(r) for r in rows)
    assert {r["criterion"] for r in rows} == {"grounded", "answers"}
    assert all(r["verdict"] is None for r in rows)


def test_hand_labels_are_matched_to_answers_by_their_opaque_id() -> None:
    runs = [run("gpt-5.4-mini", 1, ALL), run("gpt-5.4-mini", 2, ALL)]
    labels = [
        {"answer_id": answer_id("gpt-5.4-mini-r1", "policy-x-01"), "criterion": c, "verdict": v}
        for c, v in (("grounded", "pass"), ("answers", "fail"))
    ]
    text = report(runs, CASES, PRICES, labels)
    # Both judges said pass on both criteria; the hand labels agree on one of two.
    assert "| gpt-5.4-mini (effort=low) | 4/4 (100%) | 1/2 (50%) | 1/2 (50%) |" in text
