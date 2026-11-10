"""Plays one case against an assistant: sends each turn, answers each confirmation card,
and keeps everything the scorer needs.

Cards are answered from the case's `decisions`. A card the case did not plan for is
counted and answered with cancel, so the runner never confirms anything a case did not
ask for. Every card is still confirmed or cancelled as planned when its details differ
from the expected ones: the scorer records the mismatch, and the database diff shows
what the confirmation actually wrote.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.evaluation.cases import EVAL_NOW, Case, Decision
from app.agent.evaluation.database import apply_setup
from app.agent.evaluation.recorder import Failure, Recorder
from app.agent.runner import Assistant, RunStream


class InfraFailure(RuntimeError):
    """A model call failed for reasons outside the model; the case is tried again."""


@dataclass
class Segment:
    """One stretch of a run, from a message or a decision to the next card or answer."""

    kind: str  # "message" or "resume"
    turn: int
    seconds: float
    ended_with: str  # "done", "card", or "error"


@dataclass
class Card:
    turn: int
    action: str
    summary: dict[str, Any]
    decision: str
    planned: bool


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: dict[str, Any] | None

    @property
    def error_type(self) -> str | None:
        if self.result is None or self.result.get("ok", True):
            return None
        error = self.result.get("error") or {}
        return str(error.get("type")) if error.get("type") else None


@dataclass
class Transcript:
    segments: list[Segment] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    unused_decisions: int = 0
    failure: Failure | None = None

    @property
    def final_answer(self) -> str:
        return self.answers[-1] if self.answers else ""


def tool_trace(messages: list[BaseMessage]) -> list[ToolCall]:
    results: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, ToolMessage):
            try:
                parsed = json.loads(str(message.content))
            except json.JSONDecodeError:
                parsed = {"ok": False, "error": {"type": "unparsable-result"}}
            results[message.tool_call_id] = parsed if isinstance(parsed, dict) else {}
    return [
        ToolCall(call["name"], dict(call["args"]), results.get(call["id"] or ""))
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    ]


async def _drain(stream: RunStream) -> list[dict[str, Any]]:
    return [event async for event in stream.events()]


async def play(
    assistant: Assistant,
    case: Case,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    sessions: async_sessionmaker[AsyncSession],
    recorder: Recorder,
    run_timeout_seconds: float,
    quota_zone: ZoneInfo,
) -> Transcript:
    transcript = Transcript()
    for index, turn in enumerate(case.turns):
        decisions = list(turn.decisions)
        hooks_pending = bool(turn.before_decision)
        kind = "message"
        started = time.perf_counter()
        stream = await assistant.start_message(user_id, thread_id, turn.say)
        while True:
            events = await _drain(stream)
            seconds = time.perf_counter() - started
            card = next((e for e in events if e["type"] == "confirmation_required"), None)
            errors = [str(e.get("message", "")) for e in events if e["type"] == "error"]
            for event in events:
                if event["type"] == "done":
                    transcript.answers.append(str(event.get("content", "")))
                elif event["type"] == "citation":
                    transcript.citations.append(
                        {"document": event.get("document"), "heading": event.get("heading")}
                    )
            ended = "error" if errors else "card" if card is not None else "done"
            transcript.segments.append(Segment(kind, index, seconds, ended))
            if errors:
                transcript.errors.extend(errors)
                transcript.failure = _failure(recorder, seconds, run_timeout_seconds)
                if transcript.failure is Failure.INFRA:
                    raise InfraFailure(errors[0])
                transcript.unused_decisions += len(decisions)
                transcript.tool_calls = await _trace(assistant, thread_id)
                return transcript
            if card is None:
                break
            payload = card["payload"]
            planned = bool(decisions)
            decision = decisions.pop(0) if planned else Decision.CANCEL
            if planned and hooks_pending:
                await apply_setup(
                    sessions,
                    turn.before_decision,
                    now=EVAL_NOW,
                    quota_zone=quota_zone,
                )
                hooks_pending = False
            transcript.cards.append(
                Card(index, str(payload.get("action")), dict(payload["summary"]), decision, planned)
            )
            if decision is Decision.LEAVE:
                break
            kind = "resume"
            started = time.perf_counter()
            answer: Literal["confirm", "cancel"] = (
                "confirm" if decision is Decision.CONFIRM else "cancel"
            )
            stream = await assistant.start_resume(user_id, thread_id, answer)
        transcript.unused_decisions += len(decisions)
    transcript.tool_calls = await _trace(assistant, thread_id)
    return transcript


async def _trace(assistant: Assistant, thread_id: uuid.UUID) -> list[ToolCall]:
    config: RunnableConfig = {"configurable": {"thread_id": str(thread_id)}}
    state = await assistant.graph.aget_state(config)
    messages: list[BaseMessage] = state.values.get("messages", []) if state.values else []
    return tool_trace(messages)


def _failure(recorder: Recorder, seconds: float, run_timeout_seconds: float) -> Failure:
    """Why a segment ended in an error event. The run cap shows no failed model call;
    it is recognized by the segment having lasted the whole cap."""
    worst = recorder.worst_failure()
    if worst is not None:
        return worst
    if seconds >= run_timeout_seconds:
        return Failure.TIMEOUT
    return Failure.ERROR
