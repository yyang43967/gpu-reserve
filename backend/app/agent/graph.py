"""The agent graph: a model node and a tool node, with a Postgres checkpointer.

Compiled once per process. Per-run context (user, sessions, settings) arrives through
LangGraph's run context, never through graph state the model can influence.
"""

from collections.abc import Sequence
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.context import AgentContext
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools import WRITE_GATED_TOOLS, build_tools, format_tool_error

# Graph steps per run. Each model call and each tool round is one step, so this allows
# about six model calls.
RECURSION_LIMIT: Final = 12
# Approximate tokens of system prompt plus history sent to the model.
HISTORY_TOKEN_BUDGET: Final = 6000

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


def build_graph(
    model: BaseChatModel, checkpointer: BaseCheckpointSaver[Any] | None
) -> CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]:
    tools = build_tools()
    bound = model.bind_tools(tools, parallel_tool_calls=False)

    async def agent(state: AgentState) -> dict[str, Any]:
        response = await bound.ainvoke(trim_history(state["messages"]))
        assert isinstance(response, AIMessage)  # noqa: S101 - chat models return AIMessage
        return {"messages": [keep_one_write(response)]}

    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node(AGENT_NODE, agent)
    graph.add_node(TOOLS_NODE, ToolNode(tools, handle_tool_errors=format_tool_error))
    graph.add_edge(START, AGENT_NODE)
    graph.add_conditional_edges(AGENT_NODE, tools_condition, {"tools": TOOLS_NODE, END: END})
    graph.add_edge(TOOLS_NODE, AGENT_NODE)
    return graph.compile(checkpointer=checkpointer)
