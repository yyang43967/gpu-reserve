"""Assistant runs: one background task per run, streamed to the client through a queue.

A run is not tied to its HTTP response. The graph runs in a task that puts events on a
bounded queue; the SSE response only reads from it. If the client disconnects, the task
stops queueing stream events but finishes the run: it saves the answer and any pending
confirmation, and releases the thread's lease. A closed tab must never leave a confirmed
write half done.

Every run holds the thread's lease: a message, a resume, and the repair of an open tool
call before a message all happen under it, so two of them can never act on the same
thread at once.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final, Literal, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import sse
from app.agent.citations import cited_sources
from app.agent.context import AgentContext, utc_now
from app.agent.graph import AGENT_NODE, RECURSION_LIMIT
from app.agent.state import AgentState
from app.agent.tools import (
    CANCEL_RESERVATION,
    CREATE_RESERVATION,
    WRITE_GATED_TOOLS,
    dumps_result,
    record_resolution,
    stored_result,
)
from app.agent.tracing import Tracing, trace_metadata
from app.core.config import Settings
from app.core.exceptions import AssistantBusy, ModelSwitchMidTurn, NothingToConfirm
from app.models import ActionOutcome, ChatRole
from app.rag.embeddings import Embedder
from app.services import assistant_threads as threads
from app.services import audit

logger = logging.getLogger(__name__)

# A crashed process blocks its thread for at most this long.
LEASE_SECONDS: Final = 90.0
LEASE_RENEW_SECONDS: Final = 30.0
# Events buffered for a slow client before the run waits for it.
QUEUE_SIZE: Final = 256
# How long shutdown waits for runs in flight before cancelling them.
SHUTDOWN_GRACE_SECONDS: Final = 10.0

_ACTION_BY_TOOL: Final = {
    "propose_reservation": CREATE_RESERVATION,
    "cancel_my_reservation": CANCEL_RESERVATION,
}

Graph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]
Decision = Literal["confirm", "cancel"]


@dataclass
class RunStream:
    """The client's side of a run."""

    queue: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE)
    )
    consumer_gone: bool = False

    async def emit(self, event: dict[str, Any]) -> None:
        if not self.consumer_gone:
            await self.queue.put(event)

    async def close(self) -> None:
        if not self.consumer_gone:
            await self.queue.put(None)

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        try:
            while (event := await self.queue.get()) is not None:
                yield event
        finally:
            # The client went away (or the run ended). Unblock a run waiting on a full
            # queue; from now on it queues nothing.
            self.consumer_gone = True
            while not self.queue.empty():
                self.queue.get_nowait()


class Assistant:
    def __init__(
        self,
        graph: Graph,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        embedder: Embedder | None,
        tracing: Tracing | None,
        clock: Callable[[], Any] = utc_now,
        callbacks: Sequence[BaseCallbackHandler] = (),
    ) -> None:
        self.graph = graph
        self._sessions = sessions
        self._settings = settings
        self._embedder = embedder
        self._tracing = tracing
        self._clock = clock
        # Extra observers for every run, next to tracing. The evaluation runner records
        # each model call's latency, tokens, and errors through one.
        self._callbacks = list(callbacks)
        self._tasks: set[asyncio.Task[None]] = set()

    # --- entry points ---

    async def start_message(self, user_id: uuid.UUID, thread_id: uuid.UUID, text: str) -> RunStream:
        await self._claim(thread_id)
        ctx = self._context(user_id, thread_id)

        async def work(stream: RunStream) -> None:
            await threads.add_message(self._sessions, thread_id, ChatRole.USER, text, retitle=True)
            await self.repair_open_calls(ctx)
            await self._stream({"messages": [HumanMessage(text)]}, ctx, stream, kind="message")

        return self._spawn(thread_id, work)

    async def start_resume(
        self, user_id: uuid.UUID, thread_id: uuid.UUID, decision: Decision
    ) -> RunStream:
        await self._claim(thread_id)
        ctx = replace(self._context(user_id, thread_id), resuming=True)
        try:
            payload = await threads.claim_pending_action(self._sessions, thread_id)
            if payload is None:
                # A crash after a claim leaves the checkpoint at the interrupt with no
                # pending_action. It can still be resumed, under this lease.
                payload = await self._open_interrupt(thread_id)
            if payload is None:
                raise NothingToConfirm("There is no confirmation waiting in this conversation.")
        except BaseException:
            await threads.release_lease(self._sessions, thread_id)
            raise

        async def work(stream: RunStream) -> None:
            label = "Confirmed" if decision == "confirm" else "Cancelled"
            await threads.add_message(
                self._sessions,
                thread_id,
                ChatRole.USER,
                label,
                {"kind": "decision", "decision": decision, "action": payload.get("action")},
            )
            command: Command[Any] = Command(
                resume={"decision": decision, "summary": payload["summary"]}
            )
            await self._stream(command, ctx, stream, kind="resume")

        return self._spawn(thread_id, work)

    async def pending_payload(self, thread_id: uuid.UUID) -> dict[str, Any] | None:
        return await self._open_interrupt(thread_id)

    async def shutdown(self) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=SHUTDOWN_GRACE_SECONDS)
        for task in pending:
            task.cancel()

    # --- runs ---

    def _context(self, user_id: uuid.UUID, thread_id: uuid.UUID) -> AgentContext:
        return AgentContext(
            user_id=user_id,
            thread_id=thread_id,
            session_factory=self._sessions,
            settings=self._settings,
            embedder=self._embedder,
            clock=self._clock,
        )

    def _config(self, ctx: AgentContext, kind: str) -> RunnableConfig:
        config: RunnableConfig = {
            "configurable": {"thread_id": str(ctx.thread_id)},
            "recursion_limit": RECURSION_LIMIT,
            "run_name": "assistant",
            "metadata": trace_metadata(thread_id=ctx.thread_id, user_id=ctx.user_id, kind=kind),
        }
        callbacks = [*self._callbacks]
        if self._tracing is not None:
            callbacks.append(self._tracing.handler())
        if callbacks:
            config["callbacks"] = callbacks
        return config

    async def _claim(self, thread_id: uuid.UUID) -> None:
        claimed = await threads.claim_lease(
            self._sessions, thread_id, now=self._clock(), seconds=LEASE_SECONDS
        )
        if not claimed:
            raise AssistantBusy("The assistant is still working on this conversation.")

    def _spawn(
        self, thread_id: uuid.UUID, work: Callable[[RunStream], Awaitable[None]]
    ) -> RunStream:
        stream = RunStream()
        task = asyncio.create_task(self._run(thread_id, stream, work))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return stream

    async def _run(
        self,
        thread_id: uuid.UUID,
        stream: RunStream,
        work: Callable[[RunStream], Awaitable[None]],
    ) -> None:
        renewer = asyncio.create_task(self._renew(thread_id))
        try:
            await stream.emit(sse.event("status", state="started"))
            async with asyncio.timeout(self._settings.assistant_run_timeout_seconds):
                await work(stream)
        except GraphRecursionError:
            logger.warning("Assistant run on thread %s hit the recursion limit", thread_id)
            await stream.emit(
                sse.event("error", message="That took too many steps. Try a narrower request.")
            )
        except ModelSwitchMidTurn as exc:
            logger.warning("Assistant run on thread %s stopped: %s", thread_id, exc.detail)
            await stream.emit(
                sse.event(
                    "error",
                    message="The assistant model changed in the middle of this step. "
                    "Send a new message to continue.",
                )
            )
        except TimeoutError:
            logger.warning("Assistant run on thread %s timed out", thread_id)
            await stream.emit(sse.event("error", message="The assistant took too long to answer."))
        except Exception:
            logger.exception("Assistant run on thread %s failed", thread_id)
            await stream.emit(
                sse.event("error", message="The assistant ran into a problem. Please try again.")
            )
        finally:
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
            await threads.release_lease(self._sessions, thread_id)
            await stream.close()

    async def _renew(self, thread_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(LEASE_RENEW_SECONDS)
            try:
                await threads.renew_lease(
                    self._sessions, thread_id, now=self._clock(), seconds=LEASE_SECONDS
                )
            except Exception:
                logger.warning("Could not renew the lease on thread %s", thread_id, exc_info=True)

    async def _stream(
        self, graph_input: Any, ctx: AgentContext, stream: RunStream, *, kind: str
    ) -> None:
        config = self._config(ctx, kind)
        steps: list[dict[str, Any]] = []
        async for mode, chunk in self.graph.astream(
            graph_input,
            config,
            context=ctx,
            stream_mode=["messages", "custom"],
            # Each step's checkpoint is saved before the next starts.
            durability="sync",
        ):
            if mode == "messages":
                message, meta = cast(tuple[Any, dict[str, Any]], chunk)
                # Only the model's text: never tool output, never tool-call arguments.
                text = str(message.text) if isinstance(message, AIMessage) else ""
                if meta.get("langgraph_node") == AGENT_NODE and text:
                    await stream.emit(sse.event("token", text=text))
            elif mode == "custom" and isinstance(chunk, dict):
                if chunk.get("type") == "tool_end":
                    steps.append({k: chunk[k] for k in ("name", "label", "ok")})
                await stream.emit(chunk)

        state = await self.graph.aget_state(config)
        if state.interrupts:
            payload = state.interrupts[0].value
            await threads.set_pending_action(self._sessions, ctx.thread_id, ctx.user_id, payload)
            await threads.add_message(
                self._sessions,
                ctx.thread_id,
                ChatRole.ASSISTANT,
                "",
                {"tools": steps, "confirmation": payload},
            )
            await stream.emit(sse.event("confirmation_required", payload=payload))
            return

        messages = state.values.get("messages", [])
        final = messages[-1] if messages and isinstance(messages[-1], AIMessage) else None
        text = str(final.text) if final is not None else ""
        citations = cited_sources(text, state.values.get("sources") or {})
        for citation in citations:
            await stream.emit(sse.event("citation", **citation))
        await threads.add_message(
            self._sessions,
            ctx.thread_id,
            ChatRole.ASSISTANT,
            text,
            {"tools": steps, "citations": citations},
        )
        await stream.emit(sse.event("done", content=text))

    # --- open tool calls ---

    async def _open_interrupt(self, thread_id: uuid.UUID) -> dict[str, Any] | None:
        config: RunnableConfig = {"configurable": {"thread_id": str(thread_id)}}
        state = await self.graph.aget_state(config)
        if not state.interrupts:
            return None
        value: dict[str, Any] = state.interrupts[0].value
        return value

    async def repair_open_calls(self, ctx: AgentContext) -> None:
        """Answer every tool call left without a result, so the model never sees an
        assistant message whose calls have no answers (the API would reject it).

        That happens when a confirmation is still open as a new message arrives (the
        message supersedes it), after a crash after a claim, or when a run stopped
        between the model's call and the tool. A write-gated call with a stored
        resolution gets that result; a write that committed is reported, never redone.
        """
        config: RunnableConfig = {"configurable": {"thread_id": str(ctx.thread_id)}}
        state = await self.graph.aget_state(config)
        messages = state.values.get("messages", []) if state.values else []
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai is None or not last_ai.tool_calls:
            return
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        open_calls = [call for call in last_ai.tool_calls if call["id"] not in answered]
        if not open_calls:
            return

        replies: list[ToolMessage] = []
        for call in open_calls:
            call_id = call["id"] or ""
            if call["name"] in WRITE_GATED_TOOLS:
                result = await self._supersede(ctx, call_id, call["name"], call["args"])
            else:
                result = {
                    "ok": False,
                    "error": {
                        "type": "not-run",
                        "title": "Not run",
                        "detail": "This call was interrupted and did not run.",
                    },
                }
            replies.append(
                ToolMessage(dumps_result(result), tool_call_id=call_id, name=call["name"])
            )
        await self.graph.aupdate_state(config, {"messages": replies}, as_node="tools")
        await threads.clear_pending_action(self._sessions, ctx.thread_id)

    async def _supersede(
        self, ctx: AgentContext, call_id: str, tool: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        stored = await stored_result(ctx, call_id)
        if stored is not None:
            return stored
        result = {
            "ok": True,
            "result": "superseded",
            "detail": "The user sent a new message instead of confirming. Nothing changed.",
        }
        try:
            async with self._sessions() as session, session.begin():
                await record_resolution(
                    ctx,
                    session,
                    tool_call_id=call_id,
                    action=_ACTION_BY_TOOL[tool],
                    outcome=ActionOutcome.SUPERSEDED,
                    result=result,
                )
                audit.record(
                    session,
                    actor_id=ctx.user_id,
                    action="assistant.action_superseded",
                    entity_type=threads.ENTITY_THREAD,
                    entity_id=ctx.thread_id,
                    payload={"tool": tool, "args": args, "outcome": "superseded"},
                )
        except IntegrityError:
            stored = await stored_result(ctx, call_id)
            if stored is None:
                raise
            return stored
        return result
