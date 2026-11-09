"""The assistant's tools.

Every tool takes the user from the run context, never from its arguments, and calls the
same service functions as the web UI. Argument models forbid extra fields, so a model
that adds something like `user_id` gets a validation error rather than having it
silently ignored.

Read tools return `{"ok": true, ...}`. Domain errors come back as
`{"ok": false, "error": {"type", "title", "detail"}}`, using the API's problem types.

The two write-gated tools validate by dry-running the real service in a transaction
that is always rolled back, then call `interrupt()` with a summary for the user to
confirm. LangGraph resumes an interrupted tool by running it again from the top, so the
order inside them matters; see `_write_gated`.
"""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any, Final, Literal
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command, interrupt
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SkipValidation, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentContext
from app.agent.state import source_key
from app.core.exceptions import (
    DomainError,
    InvalidToken,
    PolicySearchUnavailable,
    ReservationNotFound,
)
from app.models import (
    ActionOutcome,
    AssistantAction,
    CreatedVia,
    GpuModel,
    Reservation,
    ReservationStatus,
    User,
)
from app.models.reservation import PURPOSE_MAX_LENGTH
from app.rag import retrieval
from app.services import audit, booking_rules, labs, reservations, servers, slot_search

logger = logging.getLogger(__name__)

# List results are cut to this many items; the result says how many there were.
MAX_LIST_ITEMS: Final = 20
MAX_QUERY_LENGTH: Final = 500
ENTITY_THREAD: Final = "chat_thread"

CREATE_RESERVATION: Final = "create_reservation"
CANCEL_RESERVATION: Final = "cancel_reservation"
WRITE_GATED_TOOLS: Final = frozenset({"propose_reservation", "cancel_my_reservation"})

# Summary fields that must match between what the user saw and a fresh dry run before
# a write goes ahead. GPU numbers are left out on purpose: lowest-free picking can move
# them because of an unrelated booking, and the user agreed to a count and a time.
_COMPARED: Final = {
    CREATE_RESERVATION: ("server_id", "status_after", "gpus", "start", "end", "quota_after"),
    CANCEL_RESERVATION: ("reservation_id", "effect", "gpus", "start"),
}

Runtime = ToolRuntime[AgentContext, dict[str, Any]]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    # Filled by LangGraph and hidden from the model's schema.
    runtime: Annotated[SkipValidation[Any], InjectedToolArg] = None


# --- result helpers ---


def _ok(**data: Any) -> dict[str, Any]:
    return {"ok": True, **data}


def _error(slug: str, title: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": {"type": slug, "title": title, "detail": detail}}


def _domain_error(exc: DomainError) -> dict[str, Any]:
    return _error(exc.type_slug, exc.title, exc.detail or exc.title)


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)


def _local(moment: datetime, zone: str) -> str:
    return moment.astimezone(ZoneInfo(zone)).isoformat()


def _hours(minutes: int) -> float:
    return round(minutes / 60, 1)


def _context(runtime: Runtime) -> AgentContext:
    return runtime.context


async def _load_user(session: AsyncSession, ctx: AgentContext) -> User:
    user = await session.get(User, ctx.user_id, populate_existing=True)
    if user is None or not user.is_active:
        raise InvalidToken("The account is not active.")
    return user


def _write(runtime: Runtime, kind: str, name: str, label: str, **extra: Any) -> None:
    runtime.stream_writer({"type": kind, "name": name, "label": label, **extra})


async def _guarded(
    runtime: Runtime, name: str, label: str, body: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """Runs a tool body, turning errors into structured results and reporting the step
    to the stream. Interrupts pass through untouched."""
    _write(runtime, "tool_start", name, label)
    try:
        result = await body()
    except GraphBubbleUp:
        raise
    except DomainError as exc:
        result = _domain_error(exc)
    except ValidationError as exc:
        result = _error("invalid-arguments", "Invalid arguments", str(exc))
    except Exception:
        logger.exception("Assistant tool %s failed", name)
        result = _error("internal-error", "Internal error", "The tool failed unexpectedly.")
    _write(runtime, "tool_end", name, label, ok=bool(result.get("ok")))
    return result


def format_tool_error(exc: Exception) -> str:
    """For ToolNode: argument validation errors raised before a tool body runs."""
    return dumps_result(_error("invalid-arguments", "Invalid arguments", str(exc)))


# --- read tools ---


class SearchPoliciesArgs(_Args):
    query: str = Field(
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
        description="A self-contained question or topic in the policies' terms.",
    )


async def search_policies(query: str, runtime: Runtime) -> Command[Any]:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        if ctx.embedder is None:
            raise PolicySearchUnavailable("Policy search is not configured.")
        async with ctx.session_factory() as session:
            await _load_user(session, ctx)
            hits = await retrieval.search_policies(
                session,
                ctx.embedder,
                query,
                k=ctx.settings.rag_top_k,
                min_score=ctx.settings.rag_min_score,
            )
        known: dict[str, dict[str, Any]] = dict(runtime.state.get("sources") or {})
        next_ref = max((int(s["ref"]) for s in known.values()), default=0) + 1
        added: dict[str, dict[str, Any]] = {}
        results = []
        for hit in hits:
            key = source_key(hit.document, hit.heading_path)
            source = known.get(key) or added.get(key)
            if source is None:
                source = {
                    "ref": next_ref,
                    "document": hit.document,
                    "title": hit.title,
                    "heading": hit.heading_path,
                }
                added[key] = source
                next_ref += 1
            results.append(
                {
                    "ref": source["ref"],
                    "document": hit.document,
                    "title": hit.title,
                    "heading": hit.heading_path,
                    "score": round(hit.score, 3),
                    "content": hit.content,
                }
            )
        added_sources.update(added)
        return _ok(results=results)

    added_sources: dict[str, dict[str, Any]] = {}
    result = await _guarded(runtime, "search_policies", "Searching the policies", body)
    message = ToolMessage(
        dumps_result(result), tool_call_id=runtime.tool_call_id or "", name="search_policies"
    )
    update: dict[str, Any] = {"messages": [message]}
    if added_sources:
        update["sources"] = added_sources
    return Command(update=update)


class CurrentTimeArgs(_Args):
    lab_id: uuid.UUID | None = Field(
        default=None, description="A lab id from list_servers, for that lab's local time."
    )


async def get_current_time(runtime: Runtime, lab_id: uuid.UUID | None = None) -> str:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        now = ctx.clock()
        async with ctx.session_factory() as session:
            user = await _load_user(session, ctx)
            if lab_id is not None:
                lab = await labs.get_lab(session, user, lab_id)
                zone, scope = lab.timezone, f"lab {lab.name}"
            else:
                zone, scope = ctx.settings.quota_timezone, "quota time zone"
        local = now.astimezone(ZoneInfo(zone))
        return _ok(
            now=local.isoformat(),
            weekday=local.strftime("%A"),
            timezone=zone,
            zone_of=scope,
            next_bookable_start=_local(booking_rules.ceil_to_grid(now), zone),
        )

    return dumps_result(await _guarded(runtime, "get_current_time", "Checking the time", body))


class ListServersArgs(_Args):
    gpu_model: GpuModel | None = None
    min_gpu_memory_gb: int | None = Field(default=None, ge=1, description="Memory per GPU.")
    min_gpus: int | None = Field(default=None, ge=1, description="GPUs in the server.")
    lab_id: uuid.UUID | None = None


async def list_servers(
    runtime: Runtime,
    gpu_model: GpuModel | None = None,
    min_gpu_memory_gb: int | None = None,
    min_gpus: int | None = None,
    lab_id: uuid.UUID | None = None,
) -> str:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        async with ctx.session_factory() as session:
            user = await _load_user(session, ctx)
            listing = await servers.list_servers(
                session,
                user,
                servers.ServerFilters(
                    lab_id=lab_id,
                    gpu_model=gpu_model,
                    min_gpu_memory_gb=min_gpu_memory_gb,
                    min_gpus=min_gpus,
                ),
                offset=0,
                limit=MAX_LIST_ITEMS,
            )
        return _ok(
            total=listing.total,
            servers=[
                {
                    "id": str(s.id),
                    "hostname": s.hostname,
                    "lab": {"id": str(s.lab.id), "name": s.lab.name, "timezone": s.lab.timezone},
                    "gpu_model": s.gpu_model.value,
                    "gpu_memory_gb": s.gpu_memory_gb,
                    "gpu_count": s.gpu_count,
                    "cpu_cores": s.cpu_cores,
                    "ram_gb": s.ram_gb,
                    "interconnect": s.interconnect,
                    "status": s.status.value,
                    "requires_approval": s.requires_approval,
                    "max_booking_hours": s.max_booking_hours,
                }
                for s in listing.items
            ],
        )

    return dumps_result(await _guarded(runtime, "list_servers", "Looking up servers", body))


class FindSlotsArgs(_Args):
    gpu_count: int = Field(ge=1)
    duration_minutes: int = Field(ge=30, description="A multiple of 30.")
    earliest: AwareDatetime = Field(description="ISO 8601 with offset.")
    latest: AwareDatetime = Field(description="Latest end, ISO 8601 with offset.")
    gpu_model: GpuModel | None = None
    min_gpu_memory_gb: int | None = Field(default=None, ge=1)


async def find_slots(
    runtime: Runtime,
    gpu_count: int,
    duration_minutes: int,
    earliest: datetime,
    latest: datetime,
    gpu_model: GpuModel | None = None,
    min_gpu_memory_gb: int | None = None,
) -> str:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        async with ctx.session_factory() as session:
            user = await _load_user(session, ctx)
            found = await slot_search.search_slots(
                session,
                user,
                slot_search.SlotQuery(
                    gpu_count=gpu_count,
                    duration_minutes=duration_minutes,
                    earliest=earliest,
                    latest=latest,
                    gpu_model=gpu_model,
                    min_gpu_memory_gb=min_gpu_memory_gb,
                ),
                now=ctx.clock(),
                quota_zone=ctx.settings.quota_zone,
            )
        return _ok(
            slots=[
                {
                    "server_id": str(c.server.id),
                    "hostname": c.server.hostname,
                    "lab": c.server.lab.name,
                    "timezone": c.server.lab.timezone,
                    "gpu_model": c.server.gpu_model.value,
                    "starts_at": _local(c.starts_at, c.server.lab.timezone),
                    "ends_at": _local(c.ends_at, c.server.lab.timezone),
                    "free_gpus": c.free_gpus,
                    "requires_approval": c.server.requires_approval,
                }
                for c in found[:MAX_LIST_ITEMS]
            ]
        )

    return dumps_result(await _guarded(runtime, "find_slots", "Searching for free slots", body))


class NoArgs(_Args):
    pass


async def get_my_quota(runtime: Runtime) -> str:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        async with ctx.session_factory() as session:
            user = await _load_user(session, ctx)
            quota = await reservations.quota_status(
                session, user, at=ctx.clock(), quota_zone=ctx.settings.quota_zone
            )
        return _ok(**_quota(quota))

    return dumps_result(await _guarded(runtime, "get_my_quota", "Checking your quota", body))


def _quota(quota: reservations.QuotaStatus) -> dict[str, Any]:
    return {
        "week_start": _local(quota.week.start, quota.timezone),
        "week_end": _local(quota.week.end, quota.timezone),
        "used_gpu_hours": _hours(quota.used_minutes),
        "quota_gpu_hours": _hours(quota.quota_minutes),
        "remaining_gpu_hours": _hours(quota.remaining_minutes),
    }


class ListMyReservationsArgs(_Args):
    status: ReservationStatus | None = None


def _reservation(reservation: Reservation) -> dict[str, Any]:
    zone = reservation.server.lab.timezone
    return {
        "id": str(reservation.id),
        "status": reservation.status.value,
        "server": reservation.server.hostname,
        "lab": reservation.server.lab.name,
        "timezone": zone,
        "gpu_count": reservation.gpu_count,
        "gpu_indexes": sorted(a.gpu.index for a in reservation.allocations),
        "starts_at": _local(reservation.starts_at, zone),
        "ends_at": _local(reservation.ends_at, zone),
        "purpose": reservation.purpose,
        "created_via": reservation.created_via.value,
    }


async def list_my_reservations(runtime: Runtime, status: ReservationStatus | None = None) -> str:
    async def body() -> dict[str, Any]:
        ctx = _context(runtime)
        async with ctx.session_factory() as session:
            user = await _load_user(session, ctx)
            # Always the caller's own, admins included: the filter is set explicitly,
            # and students' lists are scoped by the service anyway.
            listing = await reservations.list_reservations(
                session,
                user,
                reservations.ReservationFilters(status=status, user_id=user.id),
                offset=0,
                limit=MAX_LIST_ITEMS,
            )
        return _ok(total=listing.total, reservations=[_reservation(r) for r in listing.items])

    return dumps_result(
        await _guarded(runtime, "list_my_reservations", "Looking up your reservations", body)
    )


# --- write-gated tools ---


async def stored_result(ctx: AgentContext, tool_call_id: str) -> dict[str, Any] | None:
    """The result recorded when this tool call was resolved, if it was."""
    async with ctx.session_factory() as session:
        result: dict[str, Any] | None = await session.scalar(
            select(AssistantAction.result).where(
                AssistantAction.thread_id == ctx.thread_id,
                AssistantAction.tool_call_id == tool_call_id,
            )
        )
    return result


async def _audit(
    ctx: AgentContext,
    session: AsyncSession,
    outcome: str,
    tool: str,
    args: dict[str, Any],
    **extra: Any,
) -> None:
    audit.record(
        session,
        actor_id=ctx.user_id,
        action=f"assistant.action_{outcome}",
        entity_type=ENTITY_THREAD,
        entity_id=ctx.thread_id,
        payload={"tool": tool, "args": args, "outcome": outcome, **extra},
    )


async def _audit_now(
    ctx: AgentContext, outcome: str, tool: str, args: dict[str, Any], **extra: Any
) -> None:
    async with ctx.session_factory() as session, session.begin():
        await _audit(ctx, session, outcome, tool, args, **extra)


async def record_resolution(
    ctx: AgentContext,
    session: AsyncSession,
    *,
    tool_call_id: str,
    action: str,
    outcome: ActionOutcome,
    result: dict[str, Any],
) -> None:
    """Adds the action row to the caller's transaction; the unique key on (thread,
    tool call) makes a second resolution of the same call fail."""
    session.add(
        AssistantAction(
            thread_id=ctx.thread_id,
            tool_call_id=tool_call_id,
            user_id=ctx.user_id,
            action=action,
            outcome=outcome,
            result=result,
        )
    )
    await session.flush()


class Decision(BaseModel):
    decision: Literal["confirm", "cancel"]
    # The summary the user was shown, sent back by the API on resume.
    summary: dict[str, Any]


DryRun = Callable[[AsyncSession, User], Awaitable[dict[str, Any]]]
Write = Callable[[AsyncSession, User], Awaitable[dict[str, Any]]]


async def _write_gated(
    runtime: Runtime,
    *,
    tool: str,
    action: str,
    args: dict[str, Any],
    dry_run: DryRun,
    write: Write,
) -> dict[str, Any]:
    """The confirmation protocol shared by the write-gated tools.

    1. A stored resolution for this tool call wins: return it and do nothing else. This
       covers a resume after the write committed but the checkpoint did not.
    2. Dry-run the service against current data, in a transaction that is rolled back.
       A refusal is audited and returned to the model.
    3. interrupt(). The first time, this stops the run and the API shows the card. On
       resume it returns the user's decision and the summary they saw.
    4. On confirm, compare the fresh dry run with what the user saw. Any difference in
       the compared fields: no write, a `proposal-changed` error.
    5. Write through the service, together with the action row and an audit row, in one
       transaction.
    """
    ctx = _context(runtime)
    tool_call_id = runtime.tool_call_id or ""
    stored = await stored_result(ctx, tool_call_id)
    if stored is not None:
        return stored

    stage = "confirm" if ctx.resuming else "propose"
    try:
        async with ctx.session_factory() as session:
            try:
                summary = await dry_run(session, await _load_user(session, ctx))
            finally:
                await session.rollback()
    except DomainError as exc:
        await _audit_now(ctx, "refused", tool, args, stage=stage, error=exc.type_slug)
        raise

    raw = interrupt({"action": action, "tool_call_id": tool_call_id, "summary": summary})
    decision = Decision.model_validate(raw)

    if decision.decision == "cancel":
        result = _ok(result="cancelled_by_user", detail="The user cancelled. Nothing changed.")
        return await _resolve(
            ctx, tool_call_id, action, ActionOutcome.CANCELLED, result, tool, args
        )

    changed = [
        field for field in _COMPARED[action] if summary.get(field) != decision.summary.get(field)
    ]
    if changed:
        await _audit_now(ctx, "proposal_changed", tool, args, changed=changed)
        return _error(
            "proposal-changed",
            "The proposal changed",
            "Something changed since the user saw the confirmation card ("
            + ", ".join(changed)
            + "), so nothing was written. Current details: "
            + json.dumps({f: summary.get(f) for f in changed}, default=str),
        )

    try:
        async with ctx.session_factory() as session, session.begin():
            result = await write(session, await _load_user(session, ctx))
            await record_resolution(
                ctx,
                session,
                tool_call_id=tool_call_id,
                action=action,
                outcome=ActionOutcome.CONFIRMED,
                result=result,
            )
            await _audit(
                ctx, session, "confirmed", tool, args, reservation_id=result["reservation"]["id"]
            )
    except IntegrityError:
        # Another run resolved this call first; its write committed and ours rolled back.
        winner = await stored_result(ctx, tool_call_id)
        if winner is None:
            raise
        return winner
    except DomainError as exc:
        await _audit_now(ctx, "failed", tool, args, error=exc.type_slug)
        raise
    return result


async def _resolve(
    ctx: AgentContext,
    tool_call_id: str,
    action: str,
    outcome: ActionOutcome,
    result: dict[str, Any],
    tool: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    try:
        async with ctx.session_factory() as session, session.begin():
            await record_resolution(
                ctx,
                session,
                tool_call_id=tool_call_id,
                action=action,
                outcome=outcome,
                result=result,
            )
            await _audit(ctx, session, outcome.value, tool, args)
    except IntegrityError:
        winner = await stored_result(ctx, tool_call_id)
        if winner is None:
            raise
        return winner
    return result


class ProposeReservationArgs(_Args):
    server_id: uuid.UUID = Field(description="A server id from list_servers or find_slots.")
    gpu_count: int = Field(ge=1)
    starts_at: AwareDatetime = Field(description="ISO 8601 with offset, on :00 or :30.")
    ends_at: AwareDatetime = Field(description="ISO 8601 with offset, on :00 or :30.")
    purpose: str = Field(
        min_length=1, max_length=PURPOSE_MAX_LENGTH, description="What the GPUs are for."
    )


def _booking_summary(
    reservation: Reservation, quota: reservations.QuotaStatus, purpose: str
) -> dict[str, Any]:
    zone = reservation.server.lab.timezone
    return {
        "server_id": str(reservation.server.id),
        "server": reservation.server.hostname,
        "lab": reservation.server.lab.name,
        "timezone": zone,
        "gpus": reservation.gpu_count,
        "gpu_model": reservation.server.gpu_model.value,
        "gpu_indexes": sorted(a.gpu.index for a in reservation.allocations),
        "start": _local(reservation.starts_at, zone),
        "end": _local(reservation.ends_at, zone),
        "status_after": reservation.status.value,
        "quota_after": _quota(quota),
        "purpose": purpose,
    }


async def propose_reservation(
    runtime: Runtime,
    server_id: uuid.UUID,
    gpu_count: int,
    starts_at: datetime,
    ends_at: datetime,
    purpose: str,
) -> str:
    ctx = _context(runtime)
    request = reservations.NewReservation(
        server_id=server_id,
        gpu_count=gpu_count,
        starts_at=starts_at,
        ends_at=ends_at,
        purpose=purpose,
        created_via=CreatedVia.ASSISTANT,
    )
    args = {
        "server_id": str(server_id),
        "gpu_count": gpu_count,
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "purpose": purpose,
    }

    async def book(session: AsyncSession, user: User) -> tuple[Reservation, dict[str, Any]]:
        reservation = await reservations.create_reservation(
            session, user, request, now=ctx.clock(), quota_zone=ctx.settings.quota_zone
        )
        quota = await reservations.quota_status(
            session, user, at=reservation.starts_at, quota_zone=ctx.settings.quota_zone
        )
        return reservation, _booking_summary(reservation, quota, purpose)

    async def dry_run(session: AsyncSession, user: User) -> dict[str, Any]:
        _, summary = await book(session, user)
        return summary

    async def write(session: AsyncSession, user: User) -> dict[str, Any]:
        reservation, summary = await book(session, user)
        return _ok(result="booked", reservation={"id": str(reservation.id), **summary})

    async def body() -> dict[str, Any]:
        return await _write_gated(
            runtime,
            tool="propose_reservation",
            action=CREATE_RESERVATION,
            args=args,
            dry_run=dry_run,
            write=write,
        )

    return dumps_result(
        await _guarded(runtime, "propose_reservation", "Checking the booking", body)
    )


class CancelMyReservationArgs(_Args):
    reservation_id: uuid.UUID = Field(description="An id from list_my_reservations.")


async def _own_reservation(
    session: AsyncSession, user: User, reservation_id: uuid.UUID
) -> Reservation:
    """The caller's own reservation. Anyone else's, admins included, is not found."""
    reservation = await reservations.get_reservation(session, user, reservation_id)
    if reservation.user_id != user.id:
        raise ReservationNotFound(f"No reservation with id {reservation_id}.")
    return reservation


async def cancel_my_reservation(runtime: Runtime, reservation_id: uuid.UUID) -> str:
    ctx = _context(runtime)
    args = {"reservation_id": str(reservation_id)}

    async def cancel(session: AsyncSession, user: User) -> dict[str, Any]:
        before = await _own_reservation(session, user, reservation_id)
        zone = before.server.lab.timezone
        start, original_end = before.starts_at, before.ends_at
        after = await reservations.cancel_reservation(
            session, user, reservation_id, now=ctx.clock()
        )
        quota = await reservations.quota_status(
            session, user, at=start, quota_zone=ctx.settings.quota_zone
        )
        ended_early = after.status is ReservationStatus.COMPLETED
        return {
            "reservation_id": str(reservation_id),
            "server": after.server.hostname,
            "lab": after.server.lab.name,
            "timezone": zone,
            "gpus": after.gpu_count,
            "start": _local(start, zone),
            "end": _local(original_end, zone),
            "effect": "end_early" if ended_early else "cancel",
            "new_end": _local(after.ends_at, zone) if ended_early else None,
            "quota_after": _quota(quota),
        }

    async def write(session: AsyncSession, user: User) -> dict[str, Any]:
        summary = await cancel(session, user)
        return _ok(result="cancelled", reservation={"id": str(reservation_id), **summary})

    async def body() -> dict[str, Any]:
        return await _write_gated(
            runtime,
            tool="cancel_my_reservation",
            action=CANCEL_RESERVATION,
            args=args,
            dry_run=cancel,
            write=write,
        )

    return dumps_result(
        await _guarded(runtime, "cancel_my_reservation", "Checking the cancellation", body)
    )


def build_tools() -> list[BaseTool]:
    def make(
        fn: Callable[..., Awaitable[Any]], name: str, description: str, schema: type[_Args]
    ) -> BaseTool:
        return StructuredTool.from_function(
            coroutine=fn, name=name, description=description, args_schema=schema
        )

    return [
        make(
            search_policies,
            "search_policies",
            "Search the lab's policy documents. Returns up to five sections, each with a "
            "ref to cite as [n] and a similarity score from 0 to 1.",
            SearchPoliciesArgs,
        ),
        make(
            get_current_time,
            "get_current_time",
            "The current time, in a lab's time zone when lab_id is given, otherwise in "
            "the time zone weekly quotas are counted in.",
            CurrentTimeArgs,
        ),
        make(
            list_servers,
            "list_servers",
            "Servers the user can see, with GPU model, memory per GPU, GPU count, lab, "
            "status, and whether bookings need approval.",
            ListServersArgs,
        ),
        make(
            find_slots,
            "find_slots",
            "Ranked free slots for a job between two times, applying every booking rule "
            "and the user's quota.",
            FindSlotsArgs,
        ),
        make(get_my_quota, "get_my_quota", "The user's GPU-hours this quota week.", NoArgs),
        make(
            list_my_reservations,
            "list_my_reservations",
            "The user's own reservations, newest start first.",
            ListMyReservationsArgs,
        ),
        make(
            propose_reservation,
            "propose_reservation",
            "Check a booking against every rule and ask the user to confirm it. Nothing "
            "is booked unless the user confirms on the card.",
            ProposeReservationArgs,
        ),
        make(
            cancel_my_reservation,
            "cancel_my_reservation",
            "Check that one of the user's reservations can be cancelled (or ended early "
            "if running) and ask the user to confirm. Nothing changes unless they confirm.",
            CancelMyReservationArgs,
        ),
    ]
