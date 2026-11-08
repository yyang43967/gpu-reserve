"""Policy search: nearest chunks, merged into sections, cut to k.

One pipeline serves the assistant's tool, the hit-rate gate, and the parameter sweep:

1. `nearest(query_vector, n)` returns the n chunks closest by cosine similarity. This is
   the only step that varies: pgvector here, an in-memory scan in the sweep.
2. `merge_by_section` groups chunks by (document, heading path) and scores each section
   by its best chunk, so k counts distinct sections a reader can be pointed at.
3. Sections under the minimum score are dropped, and the first k are kept.

`n = k * CHUNK_FETCH_FACTOR`, so a section split into several chunks cannot crowd the
others out before merging.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KbChunk
from app.rag.embeddings import Embedder

CHUNK_FETCH_FACTOR: Final = 2


@dataclass(frozen=True)
class ChunkHit:
    document: str
    title: str
    heading_path: str
    chunk_index: int
    content: str
    # Cosine similarity, 1 - cosine distance.
    score: float


@dataclass(frozen=True)
class Citation:
    document: str
    title: str
    heading_path: str


@dataclass(frozen=True)
class PolicyHit:
    document: str
    title: str
    heading_path: str
    # The section's retrieved chunks in document order.
    content: str
    score: float

    @property
    def citation(self) -> Citation:
        return Citation(self.document, self.title, self.heading_path)


NearestChunks = Callable[[Sequence[float], int], Awaitable[list[ChunkHit]]]


def merge_by_section(chunks: Iterable[ChunkHit]) -> list[PolicyHit]:
    """One hit per section, best score first; ties in document and heading order."""
    groups: dict[tuple[str, str], list[ChunkHit]] = {}
    for chunk in chunks:
        groups.setdefault((chunk.document, chunk.heading_path), []).append(chunk)
    hits = [
        PolicyHit(
            document=document,
            title=members[0].title,
            heading_path=heading_path,
            content="\n\n".join(c.content for c in sorted(members, key=lambda c: c.chunk_index)),
            score=max(c.score for c in members),
        )
        for (document, heading_path), members in groups.items()
    ]
    return sorted(hits, key=lambda h: (-h.score, h.document, h.heading_path))


def select_sections(chunks: Iterable[ChunkHit], *, k: int, min_score: float) -> list[PolicyHit]:
    return [hit for hit in merge_by_section(chunks) if hit.score >= min_score][:k]


async def retrieve(
    query_vector: Sequence[float], nearest: NearestChunks, *, k: int, min_score: float
) -> list[PolicyHit]:
    if k < 1:
        raise ValueError("k must be at least 1")
    chunks = await nearest(query_vector, k * CHUNK_FETCH_FACTOR)
    return select_sections(chunks, k=k, min_score=min_score)


def pgvector_nearest(session: AsyncSession) -> NearestChunks:
    """Nearest chunks by cosine distance. The score threshold is applied by the caller,
    after the LIMIT, so the index scan is never filtered part-way."""

    async def nearest(query_vector: Sequence[float], n: int) -> list[ChunkHit]:
        distance = KbChunk.embedding.cosine_distance(list(query_vector))
        result = await session.execute(
            select(
                KbChunk.document,
                KbChunk.title,
                KbChunk.heading_path,
                KbChunk.chunk_index,
                KbChunk.content,
                distance.label("distance"),
            )
            .order_by(distance)
            .limit(n)
        )
        return [
            ChunkHit(document, title, heading, index, content, 1.0 - float(dist))
            for document, title, heading, index, content, dist in result
        ]

    return nearest


async def search_policies(
    session: AsyncSession, embedder: Embedder, query: str, *, k: int, min_score: float
) -> list[PolicyHit]:
    """The k best policy sections for a question, each with its citation."""
    query = query.strip()
    if not query:
        return []
    [vector] = await embedder.embed([query])
    return await retrieve(vector, pgvector_nearest(session), k=k, min_score=min_score)
