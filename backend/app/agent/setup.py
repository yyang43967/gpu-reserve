"""Builds the assistant for the application's lifespan."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context import utc_now
from app.agent.graph import build_graph
from app.agent.runner import Assistant
from app.agent.tracing import build_tracing
from app.core.config import Settings
from app.core.exceptions import AssistantUnavailable
from app.llm.registry import build_chat_model
from app.rag.embeddings import Embedder, build_embedder

logger = logging.getLogger(__name__)

# The checkpointer's own connections, next to the application's asyncpg pool. A run
# reads and writes a few checkpoints per step, so a small pool is enough.
CHECKPOINT_POOL_MIN: int = 1
CHECKPOINT_POOL_MAX: int = 3


def checkpoint_conninfo(settings: Settings) -> str:
    url = make_url(str(settings.database_url)).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def assistant_lifespan(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    chat_model: BaseChatModel | None = None,
    embedder: Embedder | None = None,
    clock: Callable[[], datetime] = utc_now,
    callbacks: Sequence[BaseCallbackHandler] = (),
) -> AsyncIterator[Assistant | None]:
    """The assistant, or None when its model is not configured (the API then answers
    503). Tests pass a scripted model and a fake embedder; the evaluation runner also
    passes a fixed clock and a callback that records each model call."""
    try:
        model = chat_model or build_chat_model(settings)
    except AssistantUnavailable as exc:
        logger.warning("Assistant disabled: %s", exc.detail)
        yield None
        return

    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        checkpoint_conninfo(settings),
        min_size=CHECKPOINT_POOL_MIN,
        max_size=CHECKPOINT_POOL_MAX,
        # What the checkpointer requires: autocommit, no server-side prepared
        # statements, and rows as dicts.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    tracing = build_tracing(settings)
    check = asyncio.create_task(tracing.verify()) if tracing is not None else None
    assistant = Assistant(
        build_graph(model, AsyncPostgresSaver(pool)),
        sessions,
        settings,
        embedder=embedder or build_embedder(settings),
        tracing=tracing,
        clock=clock,
        callbacks=callbacks,
    )
    try:
        yield assistant
    finally:
        if check is not None:
            check.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await check
        await assistant.shutdown()
        await pool.close()
        if tracing is not None:
            tracing.shutdown()
