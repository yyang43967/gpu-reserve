"""The evaluation runner end to end with a scripted model: a template database, a copy
per case, the real assistant, and the scorer. No model API is called."""

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent.evaluation.cases import Case
from app.agent.evaluation.database import Databases
from app.agent.evaluation.runner import CaseResult, CaseRunner, Options, eval_settings, run_cases
from app.core.config import Settings
from app.llm.scripted import ScriptedChatModel
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration

ADA = "ada.lovelace@example.edu"
ALAN = "alan.turing@example.edu"
HOST = "nlp-a100-01"
# The evening after EVAL_NOW in New York (the NLP lab's zone), after peak hours.
START = "2026-11-11T19:00:00-05:00"
END = "2026-11-11T21:00:00-05:00"
OPTIONS = Options(backoff_seconds=(0.0, 0.0), concurrency=2)


@pytest.fixture(scope="module")
async def databases(postgres_url: str) -> AsyncIterator[Databases]:
    dbs = Databases(postgres_url, prefix=f"eval_{uuid.uuid4().hex[:8]}")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    await dbs.build_template(FakeEmbedder(), settings.kb_dir, quota_zone=settings.quota_zone)
    yield dbs
    await dbs.drop_template()


def runner_for(
    databases: Databases, make_settings: Callable[..., Settings], responder: Any
) -> CaseRunner:
    settings = eval_settings(make_settings(), OPTIONS)
    model = ScriptedChatModel(responder=responder)
    return CaseRunner(databases, settings, model, FakeEmbedder(), OPTIONS)


async def run_one(runner: CaseRunner, data: dict[str, Any]) -> CaseResult:
    [result] = await run_cases(runner, [Case.model_validate(data)])
    return result


# --- a scripted user of the tools ---


def _call(name: str, args: dict[str, Any] | None = None) -> AIMessage:
    return AIMessage(
        "", tool_calls=[{"name": name, "args": args or {}, "id": f"call_{uuid.uuid4().hex}"}]
    )


def booker(gpus_per_turn: list[int]) -> Callable[[list[BaseMessage]], AIMessage]:
    """Lists servers, proposes START-END on HOST with this turn's GPU count, and reports
    the outcome. A new user message starts the next turn."""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        turn = sum(isinstance(m, HumanMessage) for m in messages) - 1
        last = messages[-1]
        if isinstance(last, HumanMessage):
            return _call("list_servers")
        assert isinstance(last, ToolMessage)
        result = json.loads(str(last.content))
        if last.name == "list_servers":
            server = next(s for s in result["servers"] if s["hostname"] == HOST)
            return _call(
                "propose_reservation",
                {
                    "server_id": server["id"],
                    "gpu_count": gpus_per_turn[turn],
                    "starts_at": START,
                    "ends_at": END,
                    "purpose": "LoRA sweep",
                },
            )
        return AIMessage(f"Result: {result.get('result') or result.get('error')}")

    return respond


def booking_case(gpus: int = 2, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "book-explicit-01",
        "category": "booking",
        "user": ADA,
        "outcome": "Booked after confirmation.",
        "turns": [{"say": f"Book 2 GPUs on {HOST} {START} to {END}"}],
        "expect": {
            "calls": [
                {
                    "name": "propose_reservation",
                    "args": {"server": HOST, "gpu_count": gpus, "starts_at": START},
                }
            ],
            "forbidden": ["cancel_my_reservation"],
            "cards": [
                {
                    "action": "create_reservation",
                    "server": HOST,
                    "gpus": gpus,
                    "decision": "confirm",
                }
            ],
            "db": {
                "created": [{"user": ADA, "server": HOST, "gpu_count": gpus, "status": "approved"}]
            },
            "actions": ["confirmed"],
        },
    }
    data.update(overrides)
    return data


# --- tests ---


async def test_a_correct_booking_passes(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    result = await run_one(runner_for(databases, make_settings, booker([2])), booking_case())
    assert result.passed, result.checks
    assert result.status == "ok"
    assert result.wrong_cards == 0
    assert result.db_created[0]["gpu_count"] == 2
    assert [s["ended_with"] for s in result.segments] == ["card", "done"]
    assert len(result.model_calls) == 3


async def test_a_wrong_card_is_confirmed_counted_and_fails(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    result = await run_one(runner_for(databases, make_settings, booker([3])), booking_case())
    assert not result.passed
    assert result.wrong_cards == 1
    assert result.card_mismatches == [["gpus"]]
    # Confirmed as planned: the diff shows what the confirmation wrote.
    assert result.db_created[0]["gpu_count"] == 3
    failed = {c["name"] for c in result.checks if not c["passed"]}
    assert failed == {"call:propose_reservation", "cards", "db"}


async def test_an_unplanned_card_is_cancelled(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    clarify = {
        "id": "clarify-count-01",
        "category": "clarify",
        "user": ADA,
        "outcome": "Asks how many GPUs.",
        "turns": [{"say": "Book me some GPUs tomorrow morning"}],
        "expect": {
            "forbidden": ["propose_reservation", "cancel_my_reservation"],
            "question_terms": ["how many"],
        },
    }
    result = await run_one(runner_for(databases, make_settings, booker([2])), clarify)
    assert not result.passed
    assert result.wrong_cards == 1
    assert [c["decision"] for c in result.cards] == ["cancel"]
    assert result.db_created == []
    assert result.actions == ["cancelled"]


async def test_a_new_message_supersedes_a_card_left_open(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    data = booking_case(
        gpus=4,
        id="change-count-01",
        category="multi_turn",
        turns=[
            {"say": f"Book 2 GPUs on {HOST} {START} to {END}"},
            {"say": "Actually, make it 4 GPUs."},
        ],
    )
    data["expect"]["cards"] = [
        {"action": "create_reservation", "gpus": 2, "decision": "leave"},
        {"action": "create_reservation", "gpus": 4, "decision": "confirm"},
    ]
    data["expect"]["actions"] = ["superseded", "confirmed"]
    result = await run_one(runner_for(databases, make_settings, booker([2, 4])), data)
    assert result.passed, result.checks
    assert [c["decision"] for c in result.cards] == ["leave", "confirm"]
    assert [s["kind"] for s in result.segments] == ["message", "message", "resume"]


async def test_a_hook_runs_between_the_card_and_the_click(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    taken = {
        "op": "reserve",
        "user": ALAN,
        "server": HOST,
        "gpu_count": 4,
        "start": START,
        "end": "2026-11-11T20:00:00-05:00",
        "purpose": "Alan's eval sweep",
    }
    data = booking_case(
        id="conflict-taken-01",
        category="conflict",
        turns=[{"say": "Book it"}],
    )
    data["expect"]["cards"][0]["before_decision"] = [taken]
    data["expect"]["errors"] = ["gpu-unavailable"]
    data["expect"]["db"] = {
        "created": [{"user": ALAN, "server": HOST, "gpu_count": 4, "status": "approved"}]
    }
    data["expect"]["actions"] = []
    result = await run_one(runner_for(databases, make_settings, booker([2])), data)
    assert result.passed, result.checks


class Unavailable(Exception):
    status_code = 503


async def test_infrastructure_errors_retry_the_case_then_leave_it_out(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    fails = {"left": 1}
    book = booker([2])

    def flaky(messages: list[BaseMessage]) -> AIMessage:
        if fails["left"]:
            fails["left"] -= 1
            raise Unavailable
        return book(messages)

    result = await run_one(runner_for(databases, make_settings, flaky), booking_case())
    assert result.passed
    assert result.attempts == 2

    def down(messages: list[BaseMessage]) -> AIMessage:
        raise Unavailable

    result = await run_one(runner_for(databases, make_settings, down), booking_case())
    assert result.status == "infra"
    assert result.attempts == OPTIONS.attempts


async def test_a_timeout_is_a_model_failure_and_not_retried(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    def slow(messages: list[BaseMessage]) -> AIMessage:
        raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.example.test"))

    result = await run_one(runner_for(databases, make_settings, slow), booking_case())
    assert result.status == "timeout"
    assert result.attempts == 1
    assert not result.passed


async def test_each_case_gets_its_own_database(
    databases: Databases, make_settings: Callable[..., Settings], postgres_url: str
) -> None:
    runner = runner_for(databases, make_settings, booker([2]))
    cases = [Case.model_validate(booking_case(id=f"book-explicit-0{i}")) for i in (1, 2, 3)]
    results = await run_cases(runner, cases)
    # The same booking three times: each passes only if it saw the untouched seed data.
    assert all(r.passed for r in results), [r.checks for r in results]
    engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with engine.connect() as conn:
        left = await conn.scalar(
            text("SELECT count(*) FROM pg_database WHERE datname LIKE :p"),
            {"p": f"{databases.prefix}_case_%"},
        )
    await engine.dispose()
    assert left == 0


async def test_hitting_the_run_cap_is_a_timeout(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    options = Options(run_timeout_seconds=0.5, backoff_seconds=(0.0,))
    settings = eval_settings(make_settings(), options)

    def stalls(messages: list[BaseMessage]) -> AIMessage:
        time.sleep(1.0)
        return AIMessage("Too late.")

    runner = CaseRunner(
        databases, settings, ScriptedChatModel(responder=stalls), FakeEmbedder(), options
    )
    result = await run_one(runner, booking_case())
    assert result.status == "timeout"
    assert result.attempts == 1


async def test_a_card_left_open_at_the_end_writes_nothing(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    data = booking_case(id="leave-open-01")
    data["expect"]["cards"][0]["decision"] = "leave"
    data["expect"]["db"] = {}
    data["expect"]["actions"] = []
    result = await run_one(runner_for(databases, make_settings, booker([2])), data)
    # Scored like any other card: compared with the expected one, and the diff must
    # show that nothing was written for it.
    assert result.passed, result.checks
    assert result.open_card
    assert result.db_created == []
    assert [s["ended_with"] for s in result.segments] == ["card"]


async def test_the_step_limit_is_a_model_failure(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    def loops(messages: list[BaseMessage]) -> AIMessage:
        return _call("get_my_quota")

    result = await run_one(runner_for(databases, make_settings, loops), booking_case())
    assert result.status == "step_limit"
    assert result.attempts == 1
    assert not result.excluded


async def test_cases_past_the_run_budget_are_aborted_not_failed(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    options = Options(budget_seconds=0.5, concurrency=1, backoff_seconds=(0.0,))
    settings = eval_settings(make_settings(), options)

    def slow(messages: list[BaseMessage]) -> AIMessage:
        time.sleep(1.0)
        return AIMessage("Slow answer.")

    runner = CaseRunner(
        databases, settings, ScriptedChatModel(responder=slow), FakeEmbedder(), options
    )
    cases = [Case.model_validate(booking_case(id=f"book-slow-0{i}")) for i in (1, 2)]
    results = await run_cases(runner, cases)
    assert [r.status for r in results] == ["aborted", "aborted"]
    assert all(r.excluded for r in results)


async def test_cases_left_out_for_infrastructure_get_one_more_try_at_the_end(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    fails = {"left": OPTIONS.attempts}
    book = booker([2])

    def outage(messages: list[BaseMessage]) -> AIMessage:
        if fails["left"]:
            fails["left"] -= 1
            raise Unavailable
        return book(messages)

    result = await run_one(runner_for(databases, make_settings, outage), booking_case())
    assert result.passed
    assert result.rerun


async def test_validation_replays_expectations_through_the_services(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    from app.agent.evaluation.oracle import replay_cases

    good = booking_case(id="book-good-01")
    good["expect"]["calls"][0]["args"].update({"ends_at": END, "purpose": {"contains": "LoRA"}})
    # Claims the evening booking breaks the peak rule; the rules accept it.
    wrong = {
        "id": "peak-wrong-01",
        "category": "rule_violation",
        "user": ADA,
        "outcome": "Refused for peak hours.",
        "turns": [{"say": "Book 2 GPUs this evening"}],
        "expect": [
            {
                "calls": [
                    {
                        "name": "propose_reservation",
                        "args": {
                            "server": HOST,
                            "gpu_count": 2,
                            "starts_at": START,
                            "ends_at": END,
                            "purpose": "Evening run",
                        },
                    }
                ],
                "errors": ["peak-hours-limit"],
            },
            {"forbidden": ["propose_reservation"]},
        ],
    }
    runner = runner_for(databases, make_settings, booker([2]))
    problems = await replay_cases(runner, [Case.model_validate(good), Case.model_validate(wrong)])
    assert all(p.startswith("peak-wrong-01 outcome 1:") for p in problems), problems
    assert any("error:peak-hours-limit" in p for p in problems)
    assert any("cards" in p for p in problems)


async def test_every_committed_case_holds_against_the_services(
    databases: Databases, make_settings: Callable[..., Settings]
) -> None:
    from app.agent.evaluation.cases import load_cases
    from app.agent.evaluation.oracle import replay_cases, static_problems
    from app.agent.evaluation.record import CASES_PATH

    cases = load_cases(CASES_PATH)
    settings = make_settings()
    problems = [p for case in cases for p in static_problems(case, settings.kb_dir)]
    problems += await replay_cases(runner_for(databases, make_settings, booker([1])), cases)
    assert problems == []
