"""Optional arguments spelled as "not set", and tool calls that could not be parsed."""

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.agent.graph import AGENT_NODE, answer_unparsed_calls, route
from app.agent.tools import (
    CurrentTimeArgs,
    FindSlotsArgs,
    ListMyReservationsArgs,
    ListServersArgs,
    NoArgs,
    unset_arguments,
)


@pytest.mark.parametrize("spelling", ["None", "null", ""])
def test_an_unset_optional_argument_is_dropped(spelling: str) -> None:
    assert CurrentTimeArgs.model_validate({"lab_id": spelling}).lab_id is None
    assert ListMyReservationsArgs.model_validate({"status": spelling}).status is None
    args = ListServersArgs.model_validate(
        {"gpu_model": spelling, "lab_id": spelling, "min_gpu_memory_gb": spelling}
    )
    assert args.gpu_model is None and args.min_gpu_memory_gb is None


def test_undeclared_and_required_arguments_still_fail() -> None:
    # A tool without the parameter: still an extra field.
    with pytest.raises(ValidationError):
        NoArgs.model_validate({"lab_id": "None"})
    # A required parameter is never treated as not set.
    with pytest.raises(ValidationError):
        FindSlotsArgs.model_validate(
            {
                "gpu_count": "None",
                "duration_minutes": 60,
                "earliest": "2026-11-11T19:00:00-05:00",
                "latest": "2026-11-11T23:00:00-05:00",
            }
        )
    # A real wrong value is not touched.
    with pytest.raises(ValidationError):
        CurrentTimeArgs.model_validate({"lab_id": "nlp-l40s-01"})


def test_unset_arguments_reports_what_was_dropped() -> None:
    assert unset_arguments(CurrentTimeArgs, {"lab_id": "None"}) == ["lab_id"]
    assert unset_arguments(NoArgs, {"lab_id": "None"}) == []
    assert unset_arguments(CurrentTimeArgs, {"lab_id": "Nonesuch"}) == []


def test_unparsed_calls_are_answered_and_go_back_to_the_model() -> None:
    message = AIMessage(
        "",
        tool_calls=[{"name": "get_my_quota", "args": {}, "id": "ok-1"}],
        invalid_tool_calls=[
            {"name": "list_servers", "args": '{"lab_id": ', "id": "bad-1", "error": "JSON"}
        ],
    )
    answers = answer_unparsed_calls(message)
    assert [(m.tool_call_id, m.name) for m in answers] == [
        ("bad-1", "list_servers"),
        ("ok-1", "get_my_quota"),
    ]
    assert '"invalid-arguments"' in str(answers[0].content)
    assert '"not-run"' in str(answers[1].content)
    assert route({"messages": [message, *answers]}) == AGENT_NODE


def test_a_clean_response_is_left_alone() -> None:
    message = AIMessage("", tool_calls=[{"name": "get_my_quota", "args": {}, "id": "ok-1"}])
    assert answer_unparsed_calls(message) == []
    assert route({"messages": [message]}) == "tools"
    assert route({"messages": [AIMessage("Done.")]}) == "__end__"
    assert not isinstance(answer_unparsed_calls(AIMessage("x")), ToolMessage)
