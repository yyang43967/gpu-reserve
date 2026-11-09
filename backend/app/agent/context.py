"""Per-run context handed to the graph's nodes and tools.

The user always comes from here, filled from the authenticated request, never from
model output. Tools load the user again from `user_id` on every call.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import Embedder


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class AgentContext:
    user_id: uuid.UUID
    thread_id: uuid.UUID
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    # None when no embedding key is configured; policy search then reports an error.
    embedder: Embedder | None
    # True on a /resume run, so audit rows can tell a refusal at confirmation from one
    # at proposal.
    resuming: bool = False
    clock: Callable[[], datetime] = field(default=utc_now)
