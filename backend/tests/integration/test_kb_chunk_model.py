"""The kb_chunks table as the migrations create it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KbChunk
from app.models.kb_chunk import EMBEDDING_DIMENSIONS

pytestmark = pytest.mark.integration


def _chunk(content_hash: str, embedding: list[float] | None = None) -> KbChunk:
    return KbChunk(
        document="peak-hours",
        title="Peak hours",
        heading_path="Why the limit exists",
        chunk_index=0,
        content="text",
        token_count=1,
        embedding=embedding or [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1),
        embedding_model="test-model",
        content_hash=content_hash,
    )


async def test_hnsw_index_definition(db_session: AsyncSession) -> None:
    """The drift check compares index columns but not the access method, operator class,
    or storage parameters, so the definition is asserted here."""
    definition = await db_session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_kb_chunks_embedding_hnsw'")
    )

    assert definition == (
        "CREATE INDEX ix_kb_chunks_embedding_hnsw ON public.kb_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')"
    )


async def test_content_hash_is_unique(db_session: AsyncSession) -> None:
    db_session.add_all([_chunk("a" * 64), _chunk("a" * 64)])

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_embedding_has_fixed_dimensions(db_session: AsyncSession) -> None:
    db_session.add(_chunk("b" * 64, embedding=[1.0] * (EMBEDDING_DIMENSIONS - 1)))

    with pytest.raises(Exception, match="expected 1536 dimensions"):
        await db_session.flush()
