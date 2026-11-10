"""A chat model that answers from a function instead of a network call.

Agent tests script each turn; the end-to-end stack uses `e2e_responder`, a few fixed
rules that are enough to drive one booking through the confirmation card.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import ConfigDict, Field

Responder = Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    responder: Responder
    # Reported in each response's metadata, as real providers do.
    model_name: str = "scripted"
    # Every prompt the model received, for assertions.
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        message = self.responder(messages)
        if "model_name" not in message.response_metadata:
            metadata = {**message.response_metadata, "model_name": self.model_name}
            message = message.model_copy(update={"response_metadata": metadata})
        return ChatResult(generations=[ChatGeneration(message=message)])


def _call_id() -> str:
    return f"call_{uuid.uuid4().hex}"


def _last_tool_result(messages: list[BaseMessage]) -> tuple[str, dict[str, Any]] | None:
    last = messages[-1]
    if isinstance(last, ToolMessage):
        return str(last.name), json.loads(str(last.content))
    return None


def e2e_responder(messages: list[BaseMessage]) -> AIMessage:
    """ "book ..." lists servers, proposes 19:00-20:00 tomorrow (lab time) with one GPU on
    the first server that needs no approval, and reports the outcome."""
    tool = _last_tool_result(messages)
    if tool is None:
        last_human = next(m for m in reversed(messages) if isinstance(m, HumanMessage))
        if "book" in str(last_human.content).lower():
            return AIMessage(
                "", tool_calls=[{"name": "list_servers", "args": {}, "id": _call_id()}]
            )
        return AIMessage("In this test setup I can only book a GPU. Ask me to book one.")
    name, result = tool
    if name == "list_servers" and result.get("ok"):
        open_servers = [s for s in result["servers"] if not s["requires_approval"]]
        if not open_servers:
            return AIMessage("There is no server that can be booked without approval.")
        server = open_servers[0]
        zone = ZoneInfo(server["lab"]["timezone"])
        tomorrow = datetime.now(zone).date() + timedelta(days=1)
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 19, tzinfo=zone)
        args = {
            "server_id": server["id"],
            "gpu_count": 1,
            "starts_at": start.isoformat(),
            "ends_at": (start + timedelta(hours=1)).isoformat(),
            "purpose": "Assistant end-to-end test booking",
        }
        return AIMessage(
            "", tool_calls=[{"name": "propose_reservation", "args": args, "id": _call_id()}]
        )
    if name == "propose_reservation":
        if result.get("ok") and result.get("result") == "booked":
            return AIMessage(f"Booked {result['reservation']['server']}.")
        if result.get("ok"):
            return AIMessage("Nothing was booked.")
        return AIMessage(f"The booking was refused: {result['error']['detail']}")
    return AIMessage("Something unexpected happened.")
