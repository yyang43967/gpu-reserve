"""Assistant conversations: ownership, messages, run leases, and pending confirmations.

Each function that changes shared state runs in its own short transaction, because runs
last for many seconds and nothing should hold a row lock across a model call.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import ThreadNotFound
from app.models import ChatMessage, ChatRole, ChatThread, User
from app.models.chat import THREAD_TITLE_MAX_LENGTH
from app.services import audit

ENTITY_THREAD: Final = "chat_thread"
DEFAULT_TITLE: Final = "New conversation"

Sessions = async_sessionmaker[AsyncSession]


def title_from(text: str) -> str:
    words = " ".join(text.split())
    if len(words) <= THREAD_TITLE_MAX_LENGTH:
        return words or DEFAULT_TITLE
    return words[: THREAD_TITLE_MAX_LENGTH - 1].rstrip() + "…"


async def create_thread(session: AsyncSession, user: User, title: str | None) -> ChatThread:
    thread = ChatThread(user_id=user.id, title=title_from(title or ""))
    session.add(thread)
    await session.flush()
    await session.refresh(thread)
    return thread


@dataclass(frozen=True)
class ThreadListing:
    items: list[ChatThread]
    total: int


async def list_threads(
    session: AsyncSession, user: User, *, offset: int, limit: int
) -> ThreadListing:
    """The user's own conversations, most recently active first. Admins see only theirs
    too: conversations are private."""
    condition = ChatThread.user_id == user.id
    total = await session.scalar(select(func.count()).select_from(ChatThread).where(condition))
    rows = await session.scalars(
        select(ChatThread)
        .where(condition)
        .order_by(ChatThread.updated_at.desc(), ChatThread.id)
        .offset(offset)
        .limit(limit)
    )
    return ThreadListing(items=list(rows), total=total or 0)


async def get_thread(session: AsyncSession, user: User, thread_id: uuid.UUID) -> ChatThread:
    """The user's own conversation. Anyone else's is not found, admins included."""
    thread = await session.get(ChatThread, thread_id, populate_existing=True)
    if thread is None or thread.user_id != user.id:
        raise ThreadNotFound(f"No conversation with id {thread_id}.")
    return thread


async def list_messages(session: AsyncSession, thread: ChatThread) -> list[ChatMessage]:
    rows = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.thread_id == thread.id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    return list(rows)


async def add_message(
    sessions: Sessions,
    thread_id: uuid.UUID,
    role: ChatRole,
    content: str,
    meta: dict[str, Any] | None = None,
    *,
    retitle: bool = False,
) -> None:
    async with sessions() as session, session.begin():
        session.add(ChatMessage(thread_id=thread_id, role=role, content=content, meta=meta or {}))
        values: dict[str, Any] = {"updated_at": func.now()}
        if retitle:
            # The first message names the conversation.
            count = await session.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.thread_id == thread_id)
            )
            if count == 1:
                values["title"] = title_from(content)
        await session.execute(update(ChatThread).where(ChatThread.id == thread_id).values(values))


# --- run leases ---


async def claim_lease(
    sessions: Sessions, thread_id: uuid.UUID, *, now: datetime, seconds: float
) -> bool:
    """Take the thread for one run, unless another run holds an unexpired lease."""
    async with sessions() as session, session.begin():
        claimed = await session.scalar(
            update(ChatThread)
            .where(
                ChatThread.id == thread_id,
                or_(ChatThread.run_lease_until.is_(None), ChatThread.run_lease_until < now),
            )
            .values(run_lease_until=now + timedelta(seconds=seconds))
            .returning(ChatThread.id)
        )
    return claimed is not None


async def renew_lease(
    sessions: Sessions, thread_id: uuid.UUID, *, now: datetime, seconds: float
) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            update(ChatThread)
            .where(ChatThread.id == thread_id)
            .values(run_lease_until=now + timedelta(seconds=seconds))
        )


async def release_lease(sessions: Sessions, thread_id: uuid.UUID) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            update(ChatThread).where(ChatThread.id == thread_id).values(run_lease_until=None)
        )


# --- pending confirmations ---


async def set_pending_action(
    sessions: Sessions, thread_id: uuid.UUID, user_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    """Record the confirmation the user is being asked for, with its audit row."""
    async with sessions() as session, session.begin():
        await session.execute(
            update(ChatThread).where(ChatThread.id == thread_id).values(pending_action=payload)
        )
        audit.record(
            session,
            actor_id=user_id,
            action="assistant.action_proposed",
            entity_type=ENTITY_THREAD,
            entity_id=thread_id,
            payload={
                "tool_call_id": payload.get("tool_call_id"),
                "action": payload.get("action"),
                "summary": payload.get("summary"),
            },
        )


async def claim_pending_action(sessions: Sessions, thread_id: uuid.UUID) -> dict[str, Any] | None:
    """Take the waiting confirmation. The row lock serializes concurrent claims, and a
    claim that waited re-reads the row after the first commits, so exactly one of them
    gets the payload and the others None."""
    async with sessions() as session, session.begin():
        claimed: dict[str, Any] | None = await session.scalar(
            select(ChatThread.pending_action).where(ChatThread.id == thread_id).with_for_update()
        )
        if claimed is not None:
            await session.execute(
                update(ChatThread).where(ChatThread.id == thread_id).values(pending_action=None)
            )
    return claimed


async def clear_pending_action(sessions: Sessions, thread_id: uuid.UUID) -> None:
    async with sessions() as session, session.begin():
        await session.execute(
            update(ChatThread).where(ChatThread.id == thread_id).values(pending_action=None)
        )
