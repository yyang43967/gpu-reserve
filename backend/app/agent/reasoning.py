"""Reasoning that belongs to another model is not sent to this one.

OpenAI's encrypted reasoning and Anthropic's signed thinking blocks are stored with the
messages in the checkpoint and sent back on later calls, which the same model needs to
continue a tool loop. Another model cannot use them: OpenAI would reject or misread an
encrypted item it did not produce, and Anthropic validates signatures. When a thread
continues on a different model (a changed ASSISTANT_MODEL, or evaluations switching
models), those blocks are left out of the request. The checkpoint keeps them.

Which model wrote a message comes from `response_metadata["model_name"]`, which all
three providers fill. A message without it is left as it is: a wrong "foreign" verdict
would silently drop the current model's own reasoning on every turn, which is the worse
failure.

A switch is only allowed at a turn boundary, where the newest history starts at a user
message. Inside a tool loop, Anthropic requires the assistant message whose calls are
being answered to carry its own thinking; no other model's message can.
"""

from collections.abc import Sequence
from typing import Any, Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.core.exceptions import ModelSwitchMidTurn
from app.llm.names import same_model

# OpenAI Responses (`reasoning`, also LangChain's standard type) and Anthropic.
REASONING_BLOCK_TYPES: Final = frozenset({"reasoning", "thinking", "redacted_thinking"})
# Where some integrations keep reasoning outside the content.
REASONING_KWARGS: Final = ("reasoning", "reasoning_content")


def message_model(message: AIMessage) -> str | None:
    name = message.response_metadata.get("model_name") or message.response_metadata.get("model")
    return name if isinstance(name, str) and name else None


def is_foreign(message: AIMessage, model_name: str) -> bool:
    produced = message_model(message)
    return produced is not None and not same_model(produced, model_name)


def _is_reasoning(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in REASONING_BLOCK_TYPES


def without_reasoning(message: AIMessage) -> AIMessage:
    """A copy without reasoning; text, tool calls, and metadata stay."""
    content = message.content
    if isinstance(content, list):
        kept = [block for block in content if not _is_reasoning(block)]
        content = kept if kept else ""
    extra = {k: v for k, v in message.additional_kwargs.items() if k not in REASONING_KWARGS}
    if content == message.content and extra == message.additional_kwargs:
        return message
    return message.model_copy(update={"content": content, "additional_kwargs": extra})


def check_turn_boundary(messages: Sequence[BaseMessage], model_name: str) -> None:
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    for message in messages[last_human + 1 :]:
        if isinstance(message, AIMessage) and is_foreign(message, model_name):
            raise ModelSwitchMidTurn(
                f"This step was started by {message_model(message)!r}, not {model_name!r}."
            )


def prepare_for_model(messages: Sequence[BaseMessage], model_name: str | None) -> list[BaseMessage]:
    """The history to send to `model_name`, with other models' reasoning left out.

    Unknown models (no configured name) get the history unchanged.
    """
    if model_name is None:
        return list(messages)
    check_turn_boundary(messages, model_name)
    return [
        without_reasoning(m) if isinstance(m, AIMessage) and is_foreign(m, model_name) else m
        for m in messages
    ]
