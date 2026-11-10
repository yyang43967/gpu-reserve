"""The agent graph: a model node and a tool node, with a Postgres checkpointer.

Compiled once per process. Per-run context (user, sessions, settings) arrives through
LangGraph's run context, never through graph state the model can influence.
"""

from collections.abc import Sequence
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.context import AgentContext
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.reasoning import prepare_for_model
from app.agent.state import AgentState
from app.agent.tools import WRITE_GATED_TOOLS, build_tools, dumps_result, format_tool_error
from app.llm.names import chat_model_name

# Graph steps per run. Each model call and each tool round is one step, so this allows
# about six model calls.
RECURSION_LIMIT: Final = 12
# Approximate tokens of system prompt plus history sent to the model.
HISTORY_TOKEN_BUDGET: Final = 6000

# What the user sees when the provider declines to answer (Anthropic's `refusal` stop
# reason). The metadata keeps the stop reason, so evaluations can count refusals.
REFUSAL_TEXT: Final = "I can't help with that request."

AGENT_NODE: Final = "agent"
TOOLS_NODE: Final = "tools"


def trim_history(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The system prompt plus the most recent history within the budget, starting at a
    user message so a tool call is never separated from its result. If the current turn
    alone is over budget, it is kept whole from its user message."""
    system = SystemMessage(SYSTEM_PROMPT)
    trimmed = trim_messages(
        [system, *messages],
        max_tokens=HISTORY_TOKEN_BUDGET,
        token_counter=count_tokens_approximately,
        strategy="last",
        start_on="human",
        include_system=True,
    )
    if any(isinstance(m, HumanMessage) for m in trimmed):
        return trimmed
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    return [system, *messages[last_human:]]


def keep_one_write(message: AIMessage) -> AIMessage:
    """A write-gated call must be the only call in its message, so the run stops at one
    confirmation. If the model sent others alongside it, keep only the first write-gated
    call, in the stored message too, so the checkpoint never holds an unanswered call."""
    calls = message.tool_calls
    gated = [call for call in calls if call["name"] in WRITE_GATED_TOOLS]
    if not gated or len(calls) == 1:
        return message
    extra = {k: v for k, v in message.additional_kwargs.items() if k != "tool_calls"}
    return message.model_copy(
        update={"tool_calls": [gated[0]], "invalid_tool_calls": [], "additional_kwargs": extra}
    )


def handle_refusal(message: AIMessage) -> AIMessage:
    """A refused response may hold partial text or calls; neither is used."""
    if message.response_metadata.get("stop_reason") != "refusal":
        return message
    return AIMessage(
        REFUSAL_TEXT,
        id=message.id,
        response_metadata=message.response_metadata,
        usage_metadata=message.usage_metadata,
    )


def answer_unparsed_calls(message: AIMessage) -> list[ToolMessage]:
    """Error results for a response with tool calls whose arguments could not be parsed.

    Without them the graph would see no tool calls and end the run with an empty
    answer. Every call in the message is answered, the parsed ones too (as not run),
    so the model never has an unanswered call in its history, and the model's own
    message is kept as it was."""
    if not message.invalid_tool_calls:
        return []
    errors = [
        ToolMessage(
            dumps_result(
                {
                    "ok": False,
                    "error": {
                        "type": "invalid-arguments",
                        "title": "Invalid arguments",
                        "detail": "The arguments of this call could not be parsed as JSON. "
                        "Send the call again with valid arguments.",
                    },
                }
            ),
            tool_call_id=call.get("id") or "",
            name=call.get("name") or "unknown",
        )
        for call in message.invalid_tool_calls
    ]
    errors += [
        ToolMessage(
            dumps_result(
                {
                    "ok": False,
                    "error": {
                        "type": "not-run",
                        "title": "Not run",
                        "detail": "Another call in the same message could not be parsed, so "
                        "none of them ran. Send one call at a time.",
                    },
                }
            ),
            tool_call_id=call["id"] or "",
            name=call["name"],
        )
        for call in message.tool_calls
    ]
    return errors


def route(state: AgentState) -> str:
    """Tool calls go to the tools; answered unparsable calls go back to the model."""
    if isinstance(state["messages"][-1], ToolMessage):
        return AGENT_NODE
    return str(tools_condition(dict(state)))


def build_graph(
    model: BaseChatModel, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    tools = build_tools()
    bound = model.bind_tools(tools, parallel_tool_calls=False)
    model_name = chat_model_name(model)

    async def agent(state: AgentState) -> dict[str, Any]:
        history = prepare_for_model(trim_history(state["messages"]), model_name)
        response = await bound.ainvoke(history)
        assert isinstance(response, AIMessage)  # noqa: S101 - chat models return AIMessage
        response = handle_refusal(response)
        errors = answer_unparsed_calls(response)
        if errors:
            return {"messages": [response, *errors]}
        return {"messages": [keep_one_write(response)]}

    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node(AGENT_NODE, agent)
    graph.add_node(TOOLS_NODE, ToolNode(tools, handle_tool_errors=format_tool_error))
    graph.add_edge(START, AGENT_NODE)
    graph.add_conditional_edges(
        AGENT_NODE, route, {"tools": TOOLS_NODE, AGENT_NODE: AGENT_NODE, END: END}
    )
    graph.add_edge(TOOLS_NODE, AGENT_NODE)
    return graph.compile(checkpointer=checkpointer)
