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
        "turns": [{"say": "Book two GPUs", "decisions": ["confirm"]}],
        "expect": {
            "calls": [{"name": "propose_reservation", "args": {"server": "nlp-a100-01"}}],
            "cards": [{"action": "create_reservation", "gpus": 2}],
        },
    }
    data.update(overrides)
    return data


# --- the case format ---


def test_a_single_expectation_becomes_a_list() -> None:
    parsed = Case.model_validate(case())
    assert len(parsed.expect) == 1


def test_cards_must_match_the_planned_decisions() -> None:
    with pytest.raises(ValueError, match="1 expected cards but 0 decisions"):
        Case.model_validate(case(turns=[{"say": "Book two GPUs"}]))


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
                "cards": [{"action": "create_reservation", "reservation": "Unknown purpose"}],
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
            turns=[{"say": "Cancel my tokenizer booking", "decisions": ["confirm"]}],
            expect={
                "calls": [
                    {"name": "cancel_my_reservation", "args": {"reservation": "Tokenizer ablation"}}
                ],
                "cards": [{"action": "cancel_reservation", "reservation": "Tokenizer ablation"}],
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
