from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_sources(
    left: dict[str, dict[str, Any]] | None, right: dict[str, dict[str, Any]] | None
) -> dict[str, dict[str, Any]]:
    """Sections seen in this thread keep their entry; new ones are added."""
    return {**(left or {}), **(right or {})}


def source_key(document: str, heading: str) -> str:
    # A string, not a tuple: checkpoints are serialized with msgpack, which has no
    # tuple keys.
    return f"{document}#{heading}"


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # Policy sections retrieved in this thread: source_key -> {ref, document, title,
    # heading}. Numbers are unique per thread, so a [2] in older, trimmed history never
    # means something different from a new [2].
    sources: NotRequired[Annotated[dict[str, dict[str, Any]], merge_sources]]
