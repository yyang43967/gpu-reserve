"""Policy search against real Postgres and pgvector, with fake embeddings."""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.session import create_session_factory
from app.rag.chunking import ChunkingParams
from app.rag.ingest import ingest
from app.rag.retrieval import Citation, pgvector_nearest, search_policies
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration

KB_DIR = Path(__file__).resolve().parents[3] / "kb"


def write_doc(directory: Path, name: str, title: str, sections: dict[str, str]) -> None:
    body = "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections.items())
    (directory / f"{name}.md").write_text(
        f"---\ntitle: {title}\nlast_reviewed: 2026-11-08\n---\n\n# {title}\n\n{body}"
    )


@pytest.fixture
def factory(engine: AsyncEngine, clean_database: None) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    directory = tmp_path / "kb"
    directory.mkdir()
    write_doc(
        directory,
        "peak-hours",
        "Peak hours",
        {
            "Window": "Peak time is weekdays from nine in the morning to six in the evening.",
            "Limit": "A booking may use four GPU hours inside the peak window.",
        },
    )
    write_doc(
        directory,
        "storage",
        "Data and storage",
        {"Scratch": "Scratch disks are wiped and never backed up. Copy results off."},
    )
    return directory


async def test_best_section_first_with_citation(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    embedder = FakeEmbedder()
    await ingest(factory, embedder, kb)

    async with factory() as session:
        hits = await search_policies(session, embedder, "is scratch backed up?", k=5, min_score=0.0)

    assert hits[0].citation == Citation("storage", "Data and storage", "Scratch")
    assert hits[0].content.startswith("Scratch disks")
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    assert len(hits) == 3


async def test_minimum_score_drops_unrelated_sections(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    embedder = FakeEmbedder()
    await ingest(factory, embedder, kb)

    async with factory() as session:
        hits = await search_policies(session, embedder, "scratch backed up", k=5, min_score=0.3)
        nothing = await search_policies(session, embedder, "zebra xylophone", k=5, min_score=0.3)

    assert [h.heading_path for h in hits] == ["Scratch"]
    assert nothing == []


async def test_blank_query_returns_nothing_without_embedding(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    embedder = FakeEmbedder()

    async with factory() as session:
        assert await search_policies(session, embedder, "   ", k=5, min_score=0.0) == []
    assert embedder.calls == []


async def test_split_section_counts_once(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    directory = tmp_path / "kb"
    directory.mkdir()
    long_text = " ".join(f"Scratch sentence number {i} about scratch disks." for i in range(12))
    write_doc(
        directory,
        "storage",
        "Data and storage",
        {"Scratch": long_text, "Other": "Unrelated words here.", "Third": "More words."},
    )
    embedder = FakeEmbedder()
    report = await ingest(factory, embedder, directory, params=ChunkingParams(30, 5))
    assert report.embedded > 3

    async with factory() as session:
        hits = await search_policies(session, embedder, "scratch disks", k=3, min_score=0.0)

    # Several chunks of the long section come back as one hit, with their text merged.
    assert hits[0].heading_path == "Scratch"
    assert len({h.heading_path for h in hits}) == len(hits)
    assert hits[0].content.count("Scratch sentence") > 4


async def test_hnsw_index_matches_an_exact_scan(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """At this size the approximate index should return exactly the exact top-n."""
    embedder = FakeEmbedder()
    await ingest(factory, embedder, KB_DIR)
    queries = ["peak hours limit", "cancel a booking", "GPU memory for LoRA", "quota week"]

    for query in queries:
        [vector] = await embedder.embed([query])
        results = []
        for setting in ("enable_seqscan", "enable_indexscan"):
            async with factory() as session, session.begin():
                await session.execute(text(f"SET LOCAL {setting} = off"))
                chunks = await pgvector_nearest(session)(vector, 10)
                results.append([(c.document, c.chunk_index) for c in chunks])
        assert results[0] == results[1], query
