"""Switching a thread between models.

Provider responses come from each library's own streaming parser, fed by a mock HTTP
transport, so the messages carry exactly the metadata and blocks a live run stores in
the checkpoint. No request leaves the process.
"""

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import openai
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_openai import ChatOpenAI

from app.agent.graph import (
    HISTORY_TOKEN_BUDGET,
    REFUSAL_TEXT,
    build_graph,
    handle_refusal,
    trim_history,
)
from app.agent.reasoning import check_turn_boundary, prepare_for_model, without_reasoning
from app.core.config import Settings
from app.core.exceptions import ModelSwitchMidTurn
from app.llm.names import chat_model_name, same_model
from app.llm.registry import build_chat_model
from app.llm.scripted import ScriptedChatModel

OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_SNAPSHOT = "claude-haiku-5-5-20261001"
VLLM_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"

ENCRYPTED = "openai-encrypted-reasoning"
SIGNATURE = "anthropic-thinking-signature"
VLLM_REASONING = "vllm-plain-reasoning"


def _settings(model: str, **values: Any) -> Settings:
    return Settings(assistant_model=model, **values)


# --- responses as each library parses them ---


def _sse(events: list[dict[str, Any]], *, named: bool) -> str:
    frames = []
    for event in events:
        prefix = f"event: {event['type']}\n" if named else ""
        frames.append(f"{prefix}data: {json.dumps(event)}\n\n")
    return "".join(frames)


def _responses_stream(call_id: str) -> str:
    base = {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": OPENAI_MODEL,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }
    reasoning = {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": ENCRYPTED}
    call = {
        "id": "fc_1",
        "type": "function_call",
        "call_id": call_id,
        "name": "get_my_quota",
        "arguments": "{}",
        "status": "completed",
    }
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 3},
    }
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": {**base, "status": "in_progress", "output": []},
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 1,
            "output_index": 0,
            "item": {**reasoning, "encrypted_content": None},
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 2,
            "output_index": 0,
            "item": reasoning,
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 3,
            "output_index": 1,
            "item": {**call, "arguments": ""},
        },
        {
            "type": "response.function_call_arguments.delta",
            "sequence_number": 4,
            "output_index": 1,
            "item_id": "fc_1",
            "delta": "{}",
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 5,
            "output_index": 1,
            "item": call,
        },
        {
            "type": "response.completed",
            "sequence_number": 6,
            "response": {
                **base,
                "status": "completed",
                "output": [reasoning, call],
                "usage": usage,
            },
        },
    ]
    return _sse(events, named=True)


def _messages_stream(call_id: str) -> str:
    events: list[dict[str, Any]] = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": ANTHROPIC_SNAPSHOT,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": SIGNATURE},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": "get_my_quota",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": "{}"},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 20},
        },
        {"type": "message_stop"},
    ]
    return _sse(events, named=True)


def _chat_completions_stream(call_id: str) -> str:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": VLLM_MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    tool_call = {
        "index": 0,
        "id": call_id,
        "type": "function",
        "function": {"name": "get_my_quota", "arguments": "{}"},
    }
    events = [
        # vLLM's reasoning parser returns reasoning beside the content.
        chunk({"role": "assistant", "content": "", "reasoning": VLLM_REASONING}),
        chunk({"tool_calls": [tool_call]}),
        chunk({}, "tool_calls"),
    ]
    return _sse(events, named=False) + "data: [DONE]\n\n"


def _offline(model: ChatOpenAI, stream: str) -> ChatOpenAI:
    """The model with its SDK client answering from `stream`; nothing reaches a network."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=stream.encode(), headers={"content-type": "text/event-stream"}
        )

    client = openai.AsyncOpenAI(
        api_key="sk-test",
        base_url=model.openai_api_base,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model.root_async_client = client
    model.async_client = client.chat.completions
    return model


def _openai(stream: str) -> BaseChatModel:
    model = build_chat_model(_settings(f"openai:{OPENAI_MODEL}", openai_api_key="sk-test"))
    assert isinstance(model, ChatOpenAI)
    return _offline(model, stream)


def _anthropic(stream: str) -> BaseChatModel:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=stream.encode(), headers={"content-type": "text/event-stream"}
        )

    model = build_chat_model(
        _settings(f"anthropic:{ANTHROPIC_MODEL}", anthropic_api_key="sk-ant-test")
    )
    # The SDK client is a cached property; this one answers from the handler.
    model.__dict__["_async_client"] = anthropic.AsyncAnthropic(
        api_key="sk-ant-test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return model


def _vllm(stream: str) -> BaseChatModel:
    settings = _settings(
        f"vllm:{VLLM_MODEL}", vllm_base_url="http://vllm.test/v1", vllm_api_key="vllm-test"
    )
    model = build_chat_model(settings)
    assert isinstance(model, ChatOpenAI)
    return _offline(model, stream)


# name: (registry model id, model builder, response stream, text only its reasoning holds)
PROVIDERS: dict[str, tuple[str, Callable[[str], BaseChatModel], Callable[[str], str], str]] = {
    "openai": (OPENAI_MODEL, _openai, _responses_stream, ENCRYPTED),
    "anthropic": (ANTHROPIC_MODEL, _anthropic, _messages_stream, SIGNATURE),
    "vllm": (VLLM_MODEL, _vllm, _chat_completions_stream, VLLM_REASONING),
}


async def _response(provider: str, call_id: str) -> AIMessage:
    """What a run stores: the streamed chunks merged into one message."""
    _, build, stream, _ = PROVIDERS[provider]
    model = build(stream(call_id))
    merged = None
    async for chunk in model.astream([HumanMessage("How many hours do I have?")]):
        merged = chunk if merged is None else merged + chunk
    message = message_chunk_to_message(merged)  # type: ignore[arg-type]
    assert isinstance(message, AIMessage)
    return message


def _target(provider: str) -> BaseChatModel:
    _, build, stream, _ = PROVIDERS[provider]
    return build(stream("unused"))


async def _finished_turn(provider: str) -> list[BaseMessage]:
    """A whole earlier turn by `provider`, and the next question."""
    call = await _response(provider, "call_quota")
    answer = AIMessage("You have 16 GPU-hours left.", response_metadata=call.response_metadata)
    return [
        HumanMessage("How many hours do I have?"),
        call,
        ToolMessage('{"ok": true}', tool_call_id="call_quota", name="get_my_quota"),
        answer,
        HumanMessage("And next week?"),
    ]


def _payload(model: BaseChatModel, messages: list[BaseMessage]) -> str:
    return json.dumps(model._get_request_payload(messages), default=str)  # type: ignore[attr-defined]


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
async def test_responses_name_the_configured_model(provider: str) -> None:
    model_id, *_ = PROVIDERS[provider]
    message = await _response(provider, "call_1")

    assert same_model(message.response_metadata["model_name"], model_id)
    assert same_model(chat_model_name(_target(provider)) or "", model_id)
    assert message.tool_calls[0]["id"] == "call_1"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_a_model_keeps_its_own_reasoning(provider: str) -> None:
    model_id, _, _, marker = PROVIDERS[provider]
    history = await _finished_turn(provider)

    prepared = prepare_for_model(history, model_id)

    assert prepared == history
    assert marker in _payload(_target(provider), prepared)


@pytest.mark.parametrize(
    ("source", "target"),
    [(s, t) for s in sorted(PROVIDERS) for t in sorted(PROVIDERS) if s != t],
)
async def test_another_models_reasoning_is_left_out(source: str, target: str) -> None:
    target_id = PROVIDERS[target][0]
    history = await _finished_turn(source)

    prepared = prepare_for_model(history, target_id)
    sent = _payload(_target(target), prepared)

    for marker in (ENCRYPTED, SIGNATURE, VLLM_REASONING):
        assert marker not in sent
    # The conversation itself survives the switch.
    assert "call_quota" in sent
    assert "16 GPU-hours" in sent
    # The checkpoint's copy is untouched.
    assert history[1].content == (await _finished_turn(source))[1].content


async def test_a_switch_inside_a_tool_loop_is_refused() -> None:
    call = await _response("openai", "call_quota")
    loop = [
        HumanMessage("How many hours do I have?"),
        call,
        ToolMessage('{"ok": true}', tool_call_id="call_quota", name="get_my_quota"),
    ]

    with pytest.raises(ModelSwitchMidTurn):
        prepare_for_model(loop, ANTHROPIC_MODEL)
    # The same model continues its own loop.
    assert prepare_for_model(loop, OPENAI_MODEL) == loop
    # A dated name of the same model is not a switch.
    check_turn_boundary(loop, "gpt-5.4-mini")


def test_messages_without_a_model_name_are_left_alone() -> None:
    message = AIMessage(
        [{"type": "reasoning", "encrypted_content": ENCRYPTED}, {"type": "text", "text": "hi"}]
    )
    history = [HumanMessage("q"), message, ToolMessage("{}", tool_call_id="c", name="x")]

    assert prepare_for_model(history, ANTHROPIC_MODEL) == history
    assert prepare_for_model(history, None) == history


def test_stripping_keeps_text_and_calls() -> None:
    message = AIMessage(
        [{"type": "thinking", "thinking": "", "signature": SIGNATURE}],
        tool_calls=[{"name": "get_my_quota", "args": {}, "id": "c1"}],
        additional_kwargs={"reasoning_content": "plain", "other": 1},
        response_metadata={"model_name": ANTHROPIC_SNAPSHOT},
    )

    stripped = without_reasoning(message)

    assert stripped.content == ""
    assert stripped.tool_calls == message.tool_calls
    assert stripped.additional_kwargs == {"other": 1}
    assert stripped.response_metadata == message.response_metadata
    assert message.content[0]["signature"] == SIGNATURE  # type: ignore[index]


# --- through the graph ---


def _scripted(name: str) -> ScriptedChatModel:
    return ScriptedChatModel(responder=lambda _: AIMessage("Done."), model_name=name)


async def test_a_thread_continues_on_another_model() -> None:
    history = await _finished_turn("openai")
    model = _scripted(ANTHROPIC_MODEL)

    result = await build_graph(model, None).ainvoke({"messages": history})  # type: ignore[call-overload]

    assert result["messages"][-1].text == "Done."
    assert result["messages"][-1].response_metadata["model_name"] == ANTHROPIC_MODEL
    sent = json.dumps([m.model_dump() for m in model.calls[0]], default=str)
    assert ENCRYPTED not in sent
    assert "call_quota" in sent
    # The stored history keeps the reasoning; only the request leaves it out.
    assert ENCRYPTED in json.dumps(result["messages"][1].content)


async def test_the_graph_refuses_a_switch_inside_a_tool_loop() -> None:
    call = await _response("openai", "call_quota")
    loop = [
        HumanMessage("How many hours do I have?"),
        call,
        ToolMessage('{"ok": true}', tool_call_id="call_quota", name="get_my_quota"),
    ]
    model = _scripted(ANTHROPIC_MODEL)

    with pytest.raises(ModelSwitchMidTurn):
        await build_graph(model, None).ainvoke({"messages": loop})  # type: ignore[call-overload]
    assert model.calls == []


def test_trimming_keeps_the_question_of_a_long_tool_loop() -> None:
    earlier = [HumanMessage(f"old question {i}") for i in range(5)]
    loop: list[BaseMessage] = [HumanMessage("Find me a slot.")]
    for i in range(30):
        loop.append(
            AIMessage(
                "",
                tool_calls=[{"name": "find_slots", "args": {}, "id": f"c{i}"}],
                response_metadata={"model_name": OPENAI_MODEL},
            )
        )
        loop.append(ToolMessage("slot " * 200, tool_call_id=f"c{i}", name="find_slots"))
    history = [*earlier, *loop]

    trimmed = trim_history(history)

    assert sum(len(str(m.content)) for m in loop) > HISTORY_TOKEN_BUDGET * 4
    assert trimmed[1] is loop[0]
    assert trimmed[1:] == loop
    # So the boundary check sees the whole loop, and a different model is refused.
    with pytest.raises(ModelSwitchMidTurn):
        check_turn_boundary(trimmed, ANTHROPIC_MODEL)


def test_a_refusal_becomes_a_plain_answer() -> None:
    refused = AIMessage(
        "partial",
        tool_calls=[{"name": "get_my_quota", "args": {}, "id": "c1"}],
        response_metadata={"stop_reason": "refusal", "model_name": ANTHROPIC_SNAPSHOT},
    )

    answer = handle_refusal(refused)

    assert answer.text == REFUSAL_TEXT
    assert answer.tool_calls == []
    assert answer.response_metadata["stop_reason"] == "refusal"
    normal = AIMessage("ok", response_metadata={"stop_reason": "end_turn"})
    assert handle_refusal(normal) is normal
