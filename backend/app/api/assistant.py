import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from app.agent import sse
from app.agent.runner import Assistant, RunStream
from app.api import problem_responses
from app.api.deps import CurrentUser, RateLimiterDep, SessionDep
from app.core.exceptions import AssistantUnavailable
from app.schemas.assistant import (
    MessageCreate,
    MessageRead,
    ResumeRequest,
    ThreadCreate,
    ThreadMessages,
    ThreadRead,
)
from app.schemas.pagination import Page, PageParamsDep
from app.services import assistant_threads as threads

router = APIRouter(
    prefix="/assistant",
    tags=["assistant"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED),
)

_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_RUN = problem_responses(
    status.HTTP_404_NOT_FOUND,
    status.HTTP_409_CONFLICT,
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    status.HTTP_429_TOO_MANY_REQUESTS,
    status.HTTP_503_SERVICE_UNAVAILABLE,
)
_SSE: dict[int | str, dict[str, Any]] = {status.HTTP_200_OK: {"content": {sse.SSE_MEDIA_TYPE: {}}}}


def get_assistant(request: Request) -> Assistant | None:
    assistant: Assistant | None = request.app.state.assistant
    return assistant


AssistantDep = Annotated[Assistant | None, Depends(get_assistant)]


def _available(assistant: Assistant | None) -> Assistant:
    """Checked after ownership, so a stranger's thread is a 404 either way."""
    if assistant is None:
        raise AssistantUnavailable("The assistant's model is not configured.")
    return assistant


def _streaming(run: RunStream) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for event in run.events():
            yield sse.encode(event)

    return StreamingResponse(body(), media_type=sse.SSE_MEDIA_TYPE, headers=sse.SSE_HEADERS)


@router.post("/threads", status_code=status.HTTP_201_CREATED)
async def create_thread(
    user: CurrentUser, session: SessionDep, body: ThreadCreate | None = None
) -> ThreadRead:
    thread = await threads.create_thread(session, user, body.title if body else None)
    return ThreadRead.build(thread)


@router.get("/threads")
async def list_threads(
    user: CurrentUser, session: SessionDep, paging: PageParamsDep
) -> Page[ThreadRead]:
    """The caller's own conversations, most recently active first."""
    listing = await threads.list_threads(
        session, user, offset=paging.offset, limit=paging.page_size
    )
    return Page[ThreadRead](
        items=[ThreadRead.build(t) for t in listing.items],
        total=listing.total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.get("/threads/{thread_id}/messages", responses=_NOT_FOUND)
async def list_messages(
    thread_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> ThreadMessages:
    """The conversation as shown on the page, with the confirmation waiting for the
    user, if any."""
    thread = await threads.get_thread(session, user, thread_id)
    messages = await threads.list_messages(session, thread)
    return ThreadMessages(
        thread=ThreadRead.build(thread),
        messages=[MessageRead.build(m) for m in messages],
        pending_action=thread.pending_action,
    )


@router.post("/threads/{thread_id}/messages", responses={**_RUN, **_SSE})
async def send_message(
    thread_id: uuid.UUID,
    body: MessageCreate,
    user: CurrentUser,
    session: SessionDep,
    limiter: RateLimiterDep,
    assistant: AssistantDep,
) -> StreamingResponse:
    """Send a message and stream the answer as server-sent events. A confirmation still
    open in the conversation is superseded: nothing is written for it."""
    await threads.get_thread(session, user, thread_id)
    available = _available(assistant)
    await limiter.check_assistant(user.id)
    await limiter.check_assistant_global(now=datetime.now(UTC))
    run = await available.start_message(user.id, thread_id, body.content)
    return _streaming(run)


@router.post("/threads/{thread_id}/resume", responses={**_RUN, **_SSE})
async def resume(
    thread_id: uuid.UUID,
    body: ResumeRequest,
    user: CurrentUser,
    session: SessionDep,
    limiter: RateLimiterDep,
    assistant: AssistantDep,
) -> StreamingResponse:
    """Confirm or cancel the waiting action and stream the rest of the run. Only a
    confirmation writes anything. 409 when nothing is waiting."""
    await threads.get_thread(session, user, thread_id)
    available = _available(assistant)
    await limiter.check_assistant(user.id)
    run = await available.start_resume(user.id, thread_id, body.decision)
    return _streaming(run)
