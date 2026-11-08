"""Load the knowledge base into kb_chunks, embedding only what changed.

Each chunk is identified by a content hash over the embedding model and dimensions,
the document, its title, the heading path, and the embedded text. A run:

1. chunks every document and computes the hashes it wants;
2. embeds the hashes that are not stored yet, outside any transaction;
3. in one transaction, under a try-lock so concurrent runs never interleave, deletes
   rows whose hash is no longer wanted (edited sections and deleted documents), inserts
   the new rows, and updates the position of unchanged rows that moved.

A second run over an unchanged directory embeds and writes nothing.

    python -m app.rag.ingest [--dry-run]
"""

import argparse
import asyncio
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import KbChunk
from app.rag.chunking import Chunk, ChunkingParams, chunk_sections, load_sections
from app.rag.embeddings import Embedder
from app.services.jobs import JOB_LOCK_NAMESPACE

# Shares the jobs' two-key advisory lock space, under its own name.
INGEST_LOCK_NAME: Final = "reindex_kb"


class IngestConflictError(RuntimeError):
    """Another run changed the stored chunks while this one was embedding."""


@dataclass(frozen=True)
class IngestReport:
    embedded: int = 0
    unchanged: int = 0
    reindexed: int = 0
    deleted: int = 0
    # True when another run held the lock, so nothing was written.
    skipped: bool = False
    dry_run: bool = False


def content_hash(chunk: Chunk, *, model: str, dimensions: int) -> str:
    parts = (
        model,
        str(dimensions),
        chunk.document,
        chunk.title,
        chunk.heading_path,
        chunk.embed_text,
    )
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def wanted_chunks(
    kb_dir: Path, *, model: str, dimensions: int, params: ChunkingParams | None = None
) -> dict[str, Chunk]:
    """Hash -> chunk for the whole knowledge base. Identical chunks collapse to one."""
    chunks = chunk_sections(load_sections(kb_dir), params)
    wanted: dict[str, Chunk] = {}
    for chunk in chunks:
        wanted.setdefault(content_hash(chunk, model=model, dimensions=dimensions), chunk)
    return wanted


async def _stored_positions(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(select(KbChunk.content_hash, KbChunk.chunk_index))
    return {content_hash: index for content_hash, index in rows}


async def _try_lock(session: AsyncSession) -> bool:
    acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:namespace, hashtext(:name))"),
        {"namespace": JOB_LOCK_NAMESPACE, "name": INGEST_LOCK_NAME},
    )
    return bool(acquired)


async def ingest(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    kb_dir: Path,
    *,
    params: ChunkingParams | None = None,
    dry_run: bool = False,
) -> IngestReport:
    """Bring kb_chunks in line with the documents in `kb_dir`.

    A missing or empty directory raises KbFormatError before anything is deleted. A dry
    run reports what would change without embedding or writing.
    """
    model, dimensions = embedder.model, embedder.dimensions
    wanted = wanted_chunks(kb_dir, model=model, dimensions=dimensions, params=params)

    async with session_factory() as session:
        before = await _stored_positions(session)
    new = [h for h in wanted if h not in before]
    if dry_run:
        shifted = [h for h, c in wanted.items() if h in before and before[h] != c.chunk_index]
        return IngestReport(
            embedded=len(new),
            unchanged=len(wanted) - len(new),
            reindexed=len(shifted),
            deleted=sum(1 for h in before if h not in wanted),
            dry_run=True,
        )

    vectors = dict(zip(new, await embedder.embed([wanted[h].embed_text for h in new]), strict=True))

    async with session_factory() as session, session.begin():
        if not await _try_lock(session):
            return IngestReport(skipped=True)
        stored = await _stored_positions(session)
        missing = [h for h in wanted if h not in stored and h not in vectors]
        if missing:
            raise IngestConflictError(
                f"{len(missing)} chunks disappeared while embedding; run the ingestion again"
            )

        stale = [h for h in stored if h not in wanted]
        if stale:
            await session.execute(delete(KbChunk).where(KbChunk.content_hash.in_(stale)))

        rows = [
            {
                "document": chunk.document,
                "title": chunk.title,
                "heading_path": chunk.heading_path,
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "embedding": vectors[h],
                "embedding_model": model,
                "content_hash": h,
            }
            for h, chunk in wanted.items()
            if h not in stored
        ]
        if rows:
            await session.execute(
                insert(KbChunk).values(rows).on_conflict_do_nothing(index_elements=["content_hash"])
            )

        moved = [
            (h, chunk.chunk_index)
            for h, chunk in wanted.items()
            if h in stored and stored[h] != chunk.chunk_index
        ]
        for h, index in moved:
            await session.execute(
                update(KbChunk).where(KbChunk.content_hash == h).values(chunk_index=index)
            )

    return IngestReport(
        embedded=len(rows),
        unchanged=len(wanted) - len(rows),
        reindexed=len(moved),
        deleted=len(stale),
    )


def format_report(report: IngestReport) -> str:
    if report.skipped:
        return "skipped: another ingestion holds the lock; nothing was written"
    prefix = "dry run, would have: " if report.dry_run else ""
    return (
        f"{prefix}embedded {report.embedded}, unchanged {report.unchanged}, "
        f"reindexed {report.reindexed}, deleted {report.deleted}"
    )


async def _main(dry_run: bool) -> int:
    from app.core.config import get_settings
    from app.db.session import create_engine, create_session_factory
    from app.rag.embeddings import UnavailableEmbedder, build_embedder

    settings = get_settings()
    client = build_embedder(settings)
    if client is None and not dry_run:
        print("OPENAI_API_KEY is not set; nothing can be embedded.", file=sys.stderr)
        return 2
    embedder: Embedder = client or UnavailableEmbedder(
        settings.embedding_model, settings.embedding_dimensions
    )
    engine = create_engine(settings)
    try:
        report = await ingest(
            create_session_factory(engine), embedder, settings.kb_dir, dry_run=dry_run
        )
    finally:
        await engine.dispose()
    print(format_report(report))
    if client is not None and not dry_run:
        print(f"embedding tokens billed: {client.tokens_used}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the knowledge base into kb_chunks.")
    parser.add_argument("--dry-run", action="store_true", help="report without embedding")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.dry_run)))


if __name__ == "__main__":
    main()
