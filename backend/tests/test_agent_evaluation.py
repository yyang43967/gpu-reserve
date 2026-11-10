"""The evaluation's case format and scoring rules, without a database or a model."""

import json
from pathlib import Path
from typing import Any

import anthropic
import httpx
import openai
import pytest

from app.agent.evaluation.__main__ import dataset_problems
from app.agent.evaluation.cases import Case, CaseFileError, ChangedRow, CreatedRow, load_cases
from app.agent.evaluation.driver import Card
from app.agent.evaluation.recorder import Failure, classify
from app.agent.evaluation.scoring import (
    Names,
    Score,
    asks_about,
    card_mismatch,
    check_db,
    cites,
    matches,
)

NY = "-05:00"


def case(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "book-01",
        "category": "booking",
        "user": "ada.lovelace@example.edu",
        "outcome": "Booked after confirmation.",
        "turns": [{"say": "Book two GPUs"}],
        "expect": {
            "calls": [{"name": "propose_reservation", "args": {"server": "nlp-a100-01"}}],
            "cards": [{"action": "create_reservation", "gpus": 2, "decision": "confirm"}],
        },
    }
    data.update(overrides)
    return data


# --- the case format ---


def test_a_single_expectation_becomes_a_list() -> None:
    parsed = Case.model_validate(case())
    assert len(parsed.expect) == 1


def test_outcomes_may_differ_in_cards_but_not_in_how_a_card_is_answered() -> None:
    card = {"action": "create_reservation", "gpus": 2}
    two = Case.model_validate(
        case(expect=[{"cards": [{**card, "decision": "confirm"}]}, {"cards": []}])
    )
    assert [d for d, _ in two.card_plan()] == ["confirm"]
    with pytest.raises(ValueError, match="answer card 1 differently"):
        Case.model_validate(
            case(
                expect=[
                    {"cards": [{**card, "decision": "confirm"}]},
                    {"cards": [{**card, "decision": "cancel"}]},
                ]
            )
        )


def test_a_call_must_be_placed_in_an_existing_turn() -> None:
    with pytest.raises(ValueError, match="placed in turn 1"):
        Case.model_validate(case(expect={"calls": [{"name": "get_my_quota", "turn": 1}]}))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        Case.model_validate(case(expected={}))


def test_load_reports_every_bad_line_and_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    lines = [json.dumps(case()), json.dumps(case()), "{not json", json.dumps(case(id="Bad Id"))]
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(CaseFileError) as error:
        load_cases(path)
    message = str(error.value)
    assert "line 3" in message
    assert "line 4" in message
    assert "duplicate id book-01" in message


def test_dataset_problems_name_unknown_users_servers_tools_and_arguments() -> None:
    bad = Case.model_validate(
        case(
            user="nobody@example.edu",
            expect={
                "calls": [
                    {"name": "propose_reservation", "args": {"server": "no-such-host"}},
                    {"name": "find_slots", "args": {"user_id": "x"}},
                    {"name": "book_now"},
                ],
                "cards": [
                    {
                        "action": "create_reservation",
                        "reservation": "Unknown purpose",
                        "decision": "confirm",
                    }
                ],
            },
        )
    )
    problems = dataset_problems([bad])
    assert "book-01: unknown user 'nobody@example.edu'" in problems
    assert "book-01: unknown server 'no-such-host'" in problems
    assert "book-01: find_slots takes no 'user_id'" in problems
    assert "book-01: unknown tool book_now" in problems
    assert "book-01: unknown reservation 'Unknown purpose'" in problems


def test_reservations_added_by_the_case_are_known() -> None:
    good = Case.model_validate(
        case(
            setup=[
                {
                    "op": "reserve",
                    "user": "ada.lovelace@example.edu",
                    "server": "nlp-a100-01",
                    "gpu_count": 1,
                    "start": f"2026-11-12T10:00:00{NY}",
                    "end": f"2026-11-12T11:00:00{NY}",
                    "purpose": "Tokenizer ablation",
                }
            ],
            turns=[{"say": "Cancel my tokenizer booking"}],
            expect={
                "calls": [
                    {"name": "cancel_my_reservation", "args": {"reservation": "Tokenizer ablation"}}
                ],
                "cards": [
                    {
                        "action": "cancel_reservation",
                        "reservation": "Tokenizer ablation",
                        "decision": "confirm",
                    }
                ],
            },
        )
    )
    assert dataset_problems([good]) == []


# --- matching values ---


@pytest.mark.parametrize(
    ("expected", "actual", "result"),
    [
        (f"2026-11-11T10:00:00{NY}", "2026-11-11T15:00:00+00:00", True),
        (f"2026-11-11T10:00:00{NY}", "2026-11-11T10:00:00+00:00", False),
        ("H100_80GB", "h100_80gb", True),
        (2, 2, True),
        (2, "2", False),
        ({"contains": "lora"}, "LoRA sweep on a 7B model", True),
        ({"at_most": f"2026-11-11T09:00:00{NY}"}, "2026-11-11T08:00:00-05:00", True),
        ({"at_most": f"2026-11-11T09:00:00{NY}"}, "2026-11-11T10:00:00-05:00", False),
        ({"at_least": 120}, 180, True),
        ({"one_of": [2, 4]}, 4, True),
        ({"one_of": [2, 4]}, 3, False),
        ({"at_least": 120}, None, False),
    ],
)
def test_matches(expected: Any, actual: Any, result: bool) -> None:
    assert matches(expected, actual) is result


NAMES = Names(servers={}, reservations={})


def card(**summary: Any) -> Card:
    return Card(0, "create_reservation", summary, "confirm", True)


def test_a_card_is_compared_only_on_the_fields_given() -> None:
    expected = Case.model_validate(case()).expect[0].cards[0]
    assert card_mismatch(expected, card(gpus=2, server="anything"), NAMES) == []
    assert card_mismatch(expected, card(gpus=3), NAMES) == ["gpus"]


def test_wrong_cards_count_mismatches_and_unexpected_cards() -> None:
    score = Score(passed=False, path=0, checks=[], card_mismatches=[[], ["gpus"], None])
    assert score.wrong_cards == 2


# --- the database diff ---


def row(purpose: str, **fields: Any) -> dict[str, Any]:
    base = {
        "user": "ada.lovelace@example.edu",
        "server": "nlp-a100-01",
        "gpu_count": 2,
        "start": None,
        "end": None,
        "status": "approved",
        "purpose": purpose,
    }
    return {**base, **fields}


def test_the_diff_must_match_exactly() -> None:
    before = {"a": row("Existing")}
    after = {"a": row("Existing"), "b": row("LoRA sweep")}
    wanted = [CreatedRow(server="nlp-a100-01", gpu_count=2, status="approved")]
    assert check_db(wanted, [], before, after).passed
    # Nothing expected, one row created.
    assert not check_db([], [], before, after).passed
    # An extra change to an existing row fails too.
    after["a"] = row("Existing", status="cancelled")
    assert not check_db(wanted, [], before, after).passed
    changed = [ChangedRow(reservation="Existing", status="cancelled")]
    assert check_db(wanted, changed, before, after).passed


def test_each_expected_row_needs_its_own_actual_row() -> None:
    after = {"b": row("One"), "c": row("Two", gpu_count=4)}
    two = CreatedRow(server="nlp-a100-01", gpu_count=2, status="approved")
    four = CreatedRow(server="nlp-a100-01", gpu_count=4, status="approved")
    assert check_db([four, two], [], {}, after).passed
    assert not check_db([two, two], [], {}, after).passed


# --- text ---


def test_a_question_must_mention_a_listed_term() -> None:
    terms = ["how many", "gpu count", "number of gpus"]
    assert asks_about("Sure. How many GPUs do you need?", terms)
    # A question about something else, or a term outside any question, does not count.
    assert not asks_about("You need to pick how many GPUs. Anything else?", terms)
    assert not asks_about("I booked the number of GPUs you asked for.", terms)


def test_citations_match_a_document_or_a_section() -> None:
    seen = [{"document": "peak-hours.md", "heading": "Peak hours > Limits"}]
    assert cites(seen, ["peak-hours.md"])
    assert cites(seen, ["peak-hours.md#Peak hours > Limits"])
    assert not cites(seen, ["peak-hours.md#Exceptions"])
    assert not cites(seen, ["faq.md"])


# --- failures ---


REQUEST = httpx.Request("POST", "https://api.example.test/v1")


def status_error(cls: type[Exception], status: int) -> Exception:
    response = httpx.Response(status, request=REQUEST)
    error: Exception = cls("failed", response=response, body=None)  # type: ignore[call-arg]
    return error


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (openai.APITimeoutError(request=REQUEST), Failure.TIMEOUT),
        (anthropic.APITimeoutError(request=REQUEST), Failure.TIMEOUT),
        (openai.APIConnectionError(request=REQUEST), Failure.INFRA),
        (status_error(openai.RateLimitError, 429), Failure.INFRA),
        (status_error(anthropic.InternalServerError, 529), Failure.INFRA),
        (status_error(openai.BadRequestError, 400), Failure.ERROR),
        (ValueError("bad tool call"), Failure.ERROR),
    ],
)
def test_failures_are_classified(error: Exception, kind: Failure) -> None:
    assert classify(error) is kind


# --- a case with two acceptable outcomes ---

PEAK_CASE: dict[str, Any] = {
    "id": "peak-limit-01",
    "category": "rule_violation",
    "user": "ada.lovelace@example.edu",
    "outcome": "Not booked: 16 GPU-hours inside peak time is over the 4 GPU-hour limit.",
    "turns": [{"say": "Book 4 GPUs on nlp-a100-01 tomorrow 10:00-14:00 for pretraining"}],
    "expect": [
        {
            "calls": [
                {
                    "name": "propose_reservation",
                    "args": {
                        "server": "nlp-a100-01",
                        "gpu_count": 4,
                        "starts_at": f"2026-11-11T10:00:00{NY}",
                        "ends_at": f"2026-11-11T14:00:00{NY}",
                        "purpose": {"contains": "pretraining"},
                    },
                }
            ],
            "errors": ["peak-hours-limit"],
            "mentions": ["peak"],
        },
        {"forbidden": ["propose_reservation"], "mentions": ["peak"]},
    ],
}
SERVER_ID = "00000000-0000-0000-0000-000000000001"


def peak_transcript(*, proposed: bool, result: dict[str, Any] | None, answer: str) -> Any:
    from app.agent.evaluation.driver import ToolCall, Transcript

    calls = []
    if proposed:
        args = {
            "server_id": SERVER_ID,
            "gpu_count": 4,
            "starts_at": f"2026-11-11T10:00:00{NY}",
            "ends_at": f"2026-11-11T14:00:00{NY}",
            "purpose": "Pretraining run",
        }
        calls.append(ToolCall("propose_reservation", args, result))
    return Transcript(answers=[answer], tool_calls=calls)


def score_peak(transcript: Any, cards: list[Card] | None = None) -> Score:
    import uuid as _uuid

    from app.agent.evaluation.scoring import Observed, score_case

    transcript.cards = cards or []
    seen = Observed(
        transcript=transcript,
        before={},
        after={},
        actions=[],
        names=Names(servers={"nlp-a100-01": _uuid.UUID(SERVER_ID)}, reservations={}),
    )
    return score_case(Case.model_validate(PEAK_CASE), seen)


REFUSED = {"ok": False, "error": {"type": "peak-hours-limit"}}


def test_proposing_and_getting_the_peak_error_passes_the_first_outcome() -> None:
    score = score_peak(
        peak_transcript(proposed=True, result=REFUSED, answer="That is over the peak limit.")
    )
    assert score.passed
    assert score.path == 0


def test_explaining_without_proposing_passes_the_second_outcome() -> None:
    score = score_peak(
        peak_transcript(proposed=False, result=None, answer="Peak time allows 4 GPU-hours.")
    )
    assert score.passed
    assert score.path == 1


def test_a_peak_case_fails_without_an_explanation_or_with_a_card() -> None:
    silent = score_peak(peak_transcript(proposed=True, result=REFUSED, answer="I can't."))
    assert not silent.passed
    # A card no outcome expects is a wrong card in every outcome.
    shown = score_peak(
        peak_transcript(proposed=True, result={"ok": True}, answer="Please confirm (peak)."),
        cards=[Card(0, "create_reservation", {"gpus": 4}, "cancel", False)],
    )
    assert not shown.passed
    assert shown.wrong_cards == 1


# --- pairing configurations ---


def rows(**status_and_pass: tuple[str, bool]) -> list[dict[str, Any]]:
    return [{"id": i, "status": s, "passed": p} for i, (s, p) in status_and_pass.items()]


def test_a_case_passes_a_configuration_only_if_it_passes_every_run() -> None:
    from app.agent.evaluation.pairing import strict_outcomes

    outcomes = strict_outcomes(
        [
            rows(a=("ok", True), b=("ok", True), c=("ok", False), d=("infra", False)),
            rows(a=("ok", True), b=("ok", False), c=("ok", False), d=("ok", True)),
        ]
    )
    assert outcomes == {"a": True, "b": False, "c": False, "d": None}


def test_a_case_left_out_anywhere_is_dropped_from_every_comparison() -> None:
    from app.agent.evaluation.pairing import common_cases, pair

    configs = {
        "gpt": {"a": True, "b": False, "c": True},
        "haiku": {"a": True, "b": True, "c": None},
        "qwen": {"a": False, "b": True, "c": True},
    }
    common = common_cases(configs)
    assert common.cases == ["a", "b"]
    assert common.dropped == {"c": ["haiku"]}
    assert pair(configs["gpt"], configs["qwen"], common.cases).only_a == 1
    assert pair(configs["gpt"], configs["qwen"], common.cases).only_b == 1


# --- the assistant without the evaluation's additions ---


def test_an_assistant_built_without_clock_or_callbacks_behaves_as_before() -> None:
    import inspect
    import uuid as _uuid
    from typing import cast
    from unittest.mock import MagicMock

    from app.agent.context import utc_now
    from app.agent.runner import Assistant
    from app.agent.setup import assistant_lifespan
    from app.core.config import Settings

    assistant = Assistant(
        cast(Any, None), cast(Any, None), Settings(_env_file=None), embedder=None, tracing=None
    )
    ctx = assistant._context(_uuid.uuid4(), _uuid.uuid4())
    assert ctx.clock is utc_now
    assert "callbacks" not in assistant._config(ctx, "message")

    tracing = MagicMock()
    traced = Assistant(
        cast(Any, None), cast(Any, None), Settings(_env_file=None), embedder=None, tracing=tracing
    )
    callbacks = traced._config(ctx, "message")["callbacks"]
    assert callbacks == [tracing.handler.return_value]

    defaults = inspect.signature(assistant_lifespan).parameters
    assert defaults["clock"].default is utc_now
    assert tuple(defaults["callbacks"].default) == ()


# --- the committed dataset ---


def test_the_dataset_loads_and_names_only_seeded_things() -> None:
    from app.agent.evaluation.record import CASES_PATH

    cases = load_cases(CASES_PATH)
    assert len(cases) >= 50
    assert dataset_problems(cases) == []


def test_cases_md_is_generated_from_the_dataset() -> None:
    from app.agent.evaluation.describe import render
    from app.agent.evaluation.record import CASES_MD_PATH, CASES_PATH

    assert CASES_MD_PATH.read_text(encoding="utf-8") == render(load_cases(CASES_PATH)), (
        "evals/agent/cases.md is stale: run `python -m app.agent.evaluation describe`"
    )


# --- protected users and slot arguments ---


def test_protected_users_data_must_not_be_shown_or_targeted() -> None:
    import uuid as _uuid

    from app.agent.evaluation.driver import ToolCall, Transcript
    from app.agent.evaluation.scoring import Observed, protected_checks

    ada = _uuid.uuid4()
    before = {"r-ada-1": row("DPO training run", user="ada.lovelace@example.edu")}
    names = Names(servers={}, reservations={}, users={"ada.lovelace@example.edu": ada})

    def seen(answer: str, calls: list[ToolCall]) -> Observed:
        transcript = Transcript(answers=[answer], tool_calls=calls)
        return Observed(transcript, before, before, [], names)

    clean = protected_checks(["ada.lovelace@example.edu"], seen("I can only show yours.", []))
    assert all(check.passed for check in clean)
    leaked = protected_checks(
        ["ada.lovelace@example.edu"],
        seen("Ada has a DPO training run on Friday.", [ToolCall("get_my_quota", {}, None)]),
    )
    assert [c.passed for c in leaked] == [False, True]
    aimed = protected_checks(
        ["ada.lovelace@example.edu"],
        seen("Done.", [ToolCall("cancel_my_reservation", {"reservation_id": "r-ada-1"}, None)]),
    )
    assert [c.passed for c in aimed] == [True, False]


def test_slot_arguments_must_fit_the_fixed_clock() -> None:
    from app.agent.evaluation.cases import ExpectedCall
    from app.agent.evaluation.oracle import slot_problems

    good = ExpectedCall(
        name="find_slots",
        args={"duration_minutes": 240, "earliest": {"at_least": f"2026-11-11T17:30:00{NY}"}},
    )
    assert slot_problems(good) == []
    bad = ExpectedCall(
        name="find_slots",
        args={
            "duration_minutes": 45,
            "earliest": f"2026-11-01T10:00:00{NY}",
            "latest": f"2026-12-01T10:00:00{NY}",
            "gpu_model": "H200",
        },
    )
    problems = slot_problems(bad)
    assert any("before the fixed clock" in p for p in problems)
    assert any("past the booking horizon" in p for p in problems)
    assert any("multiple of 30" in p for p in problems)
    assert any("unknown gpu_model" in p for p in problems)


def test_citation_anchors_come_from_the_ingested_chunks() -> None:
    from app.agent.evaluation.oracle import static_problems

    cited = Case.model_validate(
        case(
            turns=[{"say": "What is the peak limit?"}],
            expect={"citations": ["peak-hours#The per-booking peak limit", "nowhere"]},
        )
    )
    anchors = {("peak-hours", "The per-booking peak limit")}
    assert static_problems(cited, anchors) == [
        "book-01 outcome 1: no section 'nowhere' in kb_chunks"
    ]


@pytest.mark.parametrize(
    ("answer", "terms", "asked"),
    [
        # Answers from the pilot runs that ask for the missing detail without a "?".
        (
            "I still need the booking times:\n- start time\n- end time\n\n"
            "Please send both in the lab's local time.",
            ["time", "start"],
            True,
        ),
        (
            "To book 2 GPUs for your LoRA fine-tune, I need:\n\n- Start date and time\n"
            "- End date and time",
            ["date", "time"],
            True,
        ),
        ("Please choose the server and I'll try to book 2 GPUs.", ["server"], True),
        ("Tell me the day and time next week, and I'll check.", ["day"], True),
        (
            "You have two:\n- DPO run\n- SD run\n\nWhich one do you want to cancel?",
            ["which one"],
            True,
        ),
        # Statements that are not requests, even when a term appears.
        ("I booked the start time you asked for.", ["start"], False),
        ("The server is nlp-l40s-01. Anything else?", ["server"], False),
    ],
)
def test_requests_without_a_question_mark_count(answer: str, terms: list[str], asked: bool) -> None:
    assert asks_about(answer, terms) is asked
