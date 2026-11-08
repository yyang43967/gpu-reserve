"""The acceptance gate: hit rate on the labeled questions with recorded embeddings.

Runs the production path end to end: ingestion into Postgres, then search_policies
through pgvector with the configured k and minimum score. No network: the embeddings
come from evals/rag/embeddings.json.gz, and a text missing from it fails the test with
instructions to re-record.
"""

from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.session import create_session_factory
from app.rag.embeddings import CachedEmbedder
from app.rag.evaluation import (
    ACCEPTANCE_PATH,
    EMBEDDINGS_PATH,
    REQUIRED_HIT_RATE,
    format_acceptance,
    load_questions,
    run_questions,
    summarize,
)
from app.rag.ingest import ingest
from app.rag.retrieval import pgvector_nearest

pytestmark = pytest.mark.integration


async def test_acceptance_hit_rate(
    engine: AsyncEngine, clean_database: None, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings()
    replay = CachedEmbedder.load(EMBEDDINGS_PATH)
    assert (replay.model, replay.dimensions) == (
        settings.embedding_model,
        settings.embedding_dimensions,
    )
    factory = create_session_factory(engine)
    await ingest(factory, replay, settings.kb_dir)
    questions = load_questions(ACCEPTANCE_PATH)

    async with factory() as session:
        outcomes = await run_questions(
            questions,
            replay,
            pgvector_nearest(session),
            k=settings.rag_top_k,
            min_score=settings.rag_min_score,
        )

    summary = summarize(outcomes)
    report = format_acceptance(outcomes, k=settings.rag_top_k, min_score=settings.rag_min_score)
    assert summary.hit_rate >= REQUIRED_HIT_RATE, report


async def test_hnsw_matches_exact_scan_on_real_embeddings(
    engine: AsyncEngine, clean_database: None, make_settings: Callable[..., Settings]
) -> None:
    settings = make_settings()
    replay = CachedEmbedder.load(EMBEDDINGS_PATH)
    factory = create_session_factory(engine)
    await ingest(factory, replay, settings.kb_dir)
    questions = load_questions(ACCEPTANCE_PATH)
    vectors = await replay.embed([q.text for q in questions])
    n = settings.rag_top_k * 2

    for q, vector in zip(questions, vectors, strict=True):
        results = []
        for setting in ("enable_seqscan", "enable_indexscan"):
            async with factory() as session, session.begin():
                await session.execute(text(f"SET LOCAL {setting} = off"))
                chunks = await pgvector_nearest(session)(vector, n)
                results.append([(c.document, c.chunk_index) for c in chunks])
        assert results[0] == results[1], q.id
