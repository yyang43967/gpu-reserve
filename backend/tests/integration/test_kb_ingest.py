"""Ingestion against real Postgres, with deterministic fake embeddings."""

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.session import create_session_factory
from app.models import KbChunk
from app.rag.chunking import HEADING_SEPARATOR as S
from app.rag.chunking import KbFormatError
from app.rag.embeddings import EmbeddingError, UnavailableEmbedder
from app.rag.ingest import INGEST_LOCK_NAME, IngestReport, ingest
from app.services.jobs import JOB_LOCK_NAMESPACE
from app.workers import tasks
from app.workers.settings import WorkerSettings
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.integration


def write_doc(directory: Path, name: str, title: str, sections: dict[str, str]) -> None:
    body = "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections.items())
    (directory / f"{name}.md").write_text(
        f"---\ntitle: {title}\nlast_reviewed: 2026-11-08\n---\n\n# {title}\n\n{body}"
    )


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    directory = tmp_path / "kb"
    directory.mkdir()
    write_doc(
        directory,
        "peak-hours",
        "Peak hours",
        {"Window": "Weekdays from nine to six.", "Limit": "Four GPU-hours per booking."},
    )
    write_doc(directory, "quota", "Quota", {"Allowance": "Forty-eight GPU-hours per week."})
    return directory


@pytest.fixture
def factory(engine: AsyncEngine, clean_database: None) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


async def rows(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, str, int]]:
    async with factory() as session:
        result = await session.execute(
            select(KbChunk.document, KbChunk.heading_path, KbChunk.chunk_index).order_by(
                KbChunk.document, KbChunk.chunk_index
            )
        )
        return [(doc, heading, index) for doc, heading, index in result]


async def test_first_run_stores_every_chunk(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    embedder = FakeEmbedder()

    report = await ingest(factory, embedder, kb)

    assert report == IngestReport(embedded=3)
    assert await rows(factory) == [
        ("peak-hours", "Window", 0),
        ("peak-hours", "Limit", 1),
        ("quota", "Allowance", 0),
    ]
    async with factory() as session:
        chunk = await session.scalar(select(KbChunk).where(KbChunk.heading_path == "Limit"))
    assert chunk is not None
    assert chunk.title == "Peak hours"
    assert chunk.content == "Four GPU-hours per booking."
    assert chunk.embedding_model == "fake-embedding"
    assert list(chunk.embedding) == pytest.approx(
        embedder.vector(f"Peak hours{S}Limit\n\nFour GPU-hours per booking."), abs=1e-6
    )


async def test_second_run_embeds_nothing(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    embedder = FakeEmbedder()

    report = await ingest(factory, embedder, kb)

    assert report == IngestReport(unchanged=3)
    assert embedder.embedded == 0


async def test_edited_section_is_the_only_one_reembedded(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    write_doc(
        kb,
        "peak-hours",
        "Peak hours",
        {"Window": "Weekdays from nine to six.", "Limit": "Four GPU-hours, summed."},
    )
    embedder = FakeEmbedder()

    report = await ingest(factory, embedder, kb)

    assert report == IngestReport(embedded=1, unchanged=2, deleted=1)
    assert embedder.calls == [[f"Peak hours{S}Limit\n\nFour GPU-hours, summed."]]
    async with factory() as session:
        contents = set(await session.scalars(select(KbChunk.content)))
    assert "Four GPU-hours per booking." not in contents


async def test_inserted_section_moves_positions_without_reembedding(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    write_doc(
        kb,
        "peak-hours",
        "Peak hours",
        {
            "Overview": "Busy daytime hours.",
            "Window": "Weekdays from nine to six.",
            "Limit": "Four GPU-hours per booking.",
        },
    )
    embedder = FakeEmbedder()

    report = await ingest(factory, embedder, kb)

    assert report == IngestReport(embedded=1, unchanged=3, reindexed=2)
    assert embedder.embedded == 1
    assert await rows(factory) == [
        ("peak-hours", "Overview", 0),
        ("peak-hours", "Window", 1),
        ("peak-hours", "Limit", 2),
        ("quota", "Allowance", 0),
    ]


async def test_deleted_document_loses_its_chunks(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    (kb / "quota.md").unlink()

    report = await ingest(factory, FakeEmbedder(), kb)

    assert report == IngestReport(unchanged=2, deleted=1)
    assert {doc for doc, _, _ in await rows(factory)} == {"peak-hours"}


async def test_model_change_reembeds_everything(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    embedder = FakeEmbedder(model="other-model")

    report = await ingest(factory, embedder, kb)

    assert report == IngestReport(embedded=3, deleted=3)
    async with factory() as session:
        models = set(await session.scalars(select(KbChunk.embedding_model)))
    assert models == {"other-model"}


async def test_title_change_reembeds_its_document(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    write_doc(kb, "quota", "Weekly quota", {"Allowance": "Forty-eight GPU-hours per week."})

    report = await ingest(factory, FakeEmbedder(), kb)

    assert report == IngestReport(embedded=1, unchanged=2, deleted=1)


async def test_missing_or_empty_directory_deletes_nothing(
    factory: async_sessionmaker[AsyncSession], kb: Path, tmp_path: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(KbFormatError):
        await ingest(factory, FakeEmbedder(), empty)
    shutil.rmtree(empty)
    with pytest.raises(KbFormatError):
        await ingest(factory, FakeEmbedder(), empty)

    assert len(await rows(factory)) == 3


async def test_dry_run_reports_without_embedding_or_writing(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    await ingest(factory, FakeEmbedder(), kb)
    (kb / "quota.md").unlink()
    write_doc(kb, "new", "New", {"Only": "Fresh text."})

    report = await ingest(factory, UnavailableEmbedder("fake-embedding", 1536), kb, dry_run=True)

    assert report == IngestReport(embedded=1, unchanged=2, deleted=1, dry_run=True)
    assert len(await rows(factory)) == 3


async def test_without_api_key_nothing_is_written(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        await ingest(factory, UnavailableEmbedder("fake-embedding", 1536), kb)

    assert await rows(factory) == []


async def test_run_while_another_holds_the_lock_is_skipped(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    async with factory() as holder, holder.begin():
        await holder.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:name))"),
            {"namespace": JOB_LOCK_NAMESPACE, "name": INGEST_LOCK_NAME},
        )
        report = await ingest(factory, FakeEmbedder(), kb)

    assert report == IngestReport(skipped=True)
    assert await rows(factory) == []


async def test_concurrent_runs_leave_one_copy(
    factory: async_sessionmaker[AsyncSession], kb: Path
) -> None:
    reports = await asyncio.gather(*(ingest(factory, FakeEmbedder(), kb) for _ in range(4)))

    assert sum(r.embedded for r in reports) == 3
    assert all(r.skipped or r.embedded == 3 or r.unchanged == 3 for r in reports)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(KbChunk)) == 3


async def test_reindex_task_runs_the_ingestion(
    factory: async_sessionmaker[AsyncSession], kb: Path, make_settings: Callable[..., Settings]
) -> None:
    ctx = {
        "settings": make_settings(kb_dir=kb),
        "session_factory": factory,
        "embedder": FakeEmbedder(),
    }

    assert await tasks.reindex_kb(ctx) == IngestReport(embedded=3)
    assert await tasks.reindex_kb(ctx) == IngestReport(unchanged=3)
    assert tasks.reindex_kb in WorkerSettings.functions


async def test_reindex_task_without_api_key_fails_clearly(
    factory: async_sessionmaker[AsyncSession], kb: Path, make_settings: Callable[..., Settings]
) -> None:
    ctx = {"settings": make_settings(kb_dir=kb), "session_factory": factory, "embedder": None}

    with pytest.raises(EmbeddingError, match="OPENAI_API_KEY"):
        await tasks.reindex_kb(ctx)
