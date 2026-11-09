"""Pure parts of the assistant: history trimming, write isolation, citations, SSE,
tool schemas, tracing metadata, and the model registry."""

import json
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent import sse
from app.agent.citations import cited_sources
from app.agent.graph import HISTORY_TOKEN_BUDGET, keep_one_write, trim_history
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import merge_sources, source_key
from app.agent.tools import WRITE_GATED_TOOLS, build_tools
from app.agent.tracing import trace_metadata
from app.core.config import Environment, Settings
from app.core.exceptions import AssistantUnavailable
from app.llm.registry import build_chat_model
from app.llm.scripted import ScriptedChatModel


def _turn(i: int, size: int) -> list[object]:
    return [
        HumanMessage(f"question {i} " + "word " * size),
        AIMessage("", tool_calls=[{"name": "get_my_quota", "args": {}, "id": f"c{i}"}]),
        ToolMessage("{}", tool_call_id=f"c{i}", name="get_my_quota"),
        AIMessage(f"answer {i}"),
    ]


def test_trimming_keeps_the_system_prompt_and_whole_turns() -> None:
    history = [m for i in range(40) for m in _turn(i, 200)]

    trimmed = trim_history(history)  # type: ignore[arg-type]

    assert isinstance(trimmed[0], SystemMessage)
    assert trimmed[0].content == SYSTEM_PROMPT
    assert isinstance(trimmed[1], HumanMessage)
    assert len(trimmed) < len(history)
    assert trimmed[-1] is history[-1]
    answered = {m.tool_call_id for m in trimmed if isinstance(m, ToolMessage)}
    calls = {c["id"] for m in trimmed if isinstance(m, AIMessage) for c in m.tool_calls}
    assert calls == answered


def test_an_oversized_current_turn_is_kept_whole() -> None:
    history = [*_turn(0, 10), *_turn(1, HISTORY_TOKEN_BUDGET * 2)]

    trimmed = trim_history(history)  # type: ignore[arg-type]

    assert isinstance(trimmed[0], SystemMessage)
    assert trimmed[1:] == history[4:]


def test_a_write_gated_call_is_kept_alone() -> None:
    message = AIMessage(
        "",
        tool_calls=[
            {"name": "get_my_quota", "args": {}, "id": "a"},
            {"name": "propose_reservation", "args": {"x": 1}, "id": "b"},
            {"name": "cancel_my_reservation", "args": {}, "id": "c"},
        ],
        additional_kwargs={"tool_calls": ["raw"], "other": 1},
    )

    kept = keep_one_write(message)

    assert [c["id"] for c in kept.tool_calls] == ["b"]
    assert kept.additional_kwargs == {"other": 1}
    assert kept.id == message.id


def test_read_calls_together_are_left_alone() -> None:
    message = AIMessage(
        "",
        tool_calls=[
            {"name": "get_my_quota", "args": {}, "id": "a"},
            {"name": "list_servers", "args": {}, "id": "b"},
        ],
    )
    assert keep_one_write(message) is message


def test_citations_follow_the_answer_and_ignore_unknown_refs() -> None:
    sources = {
        source_key("a", "One"): {"ref": 1, "document": "a", "title": "A", "heading": "One"},
        source_key("b", "Two"): {"ref": 2, "document": "b", "title": "B", "heading": "Two"},
    }

    cited = cited_sources("See [2] and [7], then [1] and [2] again.", sources)

    assert [c["ref"] for c in cited] == [2, 1]
    assert cited[0] == {"ref": 2, "document": "b", "title": "B", "heading": "Two"}


def test_sources_merge_keeps_existing_numbers() -> None:
    old = {"a#x": {"ref": 1}}
    assert merge_sources(old, {"b#y": {"ref": 2}}) == {"a#x": {"ref": 1}, "b#y": {"ref": 2}}
    assert merge_sources(None, None) == {}


def test_sse_frames() -> None:
    frame = sse.encode(sse.event("token", text="héllo\n"))
    assert frame.startswith(b"data: ") and frame.endswith(b"\n\n")
    assert json.loads(frame[6:]) == {"type": "token", "text": "héllo\n"}
    assert sse.SSE_HEADERS["X-Accel-Buffering"] == "no"


def test_no_tool_takes_a_user() -> None:
    tools = build_tools()
    assert {t.name for t in tools} >= WRITE_GATED_TOOLS
    for tool in tools:
        properties = tool.tool_call_schema.model_json_schema().get("properties", {})
        assert not {"user", "user_id", "runtime", "actor"} & set(properties), tool.name


def test_trace_metadata_has_ids_only() -> None:
    thread, user = uuid.uuid4(), uuid.uuid4()
    meta = trace_metadata(thread_id=thread, user_id=user, kind="message")
    assert meta["langfuse_session_id"] == str(thread)
    assert meta["langfuse_user_id"] == str(user)
    assert "@" not in json.dumps(meta)


def test_registry() -> None:
    with pytest.raises(AssistantUnavailable):
        build_chat_model(Settings(assistant_model="openai:x"))  # no key in tests
    with pytest.raises(AssistantUnavailable):
        build_chat_model(Settings(assistant_model="nonsense"))
    with pytest.raises(AssistantUnavailable):
        build_chat_model(Settings(assistant_model="scripted:e2e"))
    model = build_chat_model(Settings(assistant_model="scripted:e2e", environment=Environment.TEST))
    assert isinstance(model, ScriptedChatModel)
    chat = build_chat_model(
        Settings(assistant_model="openai:gpt-test", openai_api_key="sk-test")  # type: ignore[arg-type]
    )
    assert chat.model_name == "gpt-test"  # type: ignore[attr-defined]
