from collections.abc import Sequence

import pytest

from app.rag.retrieval import (
    CHUNK_FETCH_FACTOR,
    ChunkHit,
    Citation,
    merge_by_section,
    retrieve,
    select_sections,
)


def hit(document: str, heading: str, index: int, score: float, content: str = "") -> ChunkHit:
    return ChunkHit(document, document.title(), heading, index, content or heading, score)


def test_chunks_of_one_section_merge_in_document_order() -> None:
    chunks = [
        hit("quota", "Default", 3, 0.6, "second part"),
        hit("peak", "Limit", 0, 0.7),
        hit("quota", "Default", 2, 0.9, "first part"),
    ]

    merged = merge_by_section(chunks)

    assert [(h.document, h.heading_path, h.score) for h in merged] == [
        ("quota", "Default", 0.9),
        ("peak", "Limit", 0.7),
    ]
    assert merged[0].content == "first part\n\nsecond part"
    assert merged[0].citation == Citation("quota", "Quota", "Default")


def test_ties_are_ordered_by_document_and_heading() -> None:
    merged = merge_by_section([hit("b", "X", 0, 0.5), hit("a", "Y", 0, 0.5), hit("a", "X", 1, 0.5)])

    assert [(h.document, h.heading_path) for h in merged] == [("a", "X"), ("a", "Y"), ("b", "X")]


def test_minimum_score_then_k_sections() -> None:
    chunks = [hit("d", f"S{i}", i, 0.9 - i * 0.1) for i in range(6)]

    selected = select_sections(chunks, k=3, min_score=0.65)

    assert [h.heading_path for h in selected] == ["S0", "S1", "S2"]
    assert [h.heading_path for h in select_sections(chunks, k=5, min_score=0.65)] == [
        "S0",
        "S1",
        "S2",
    ]


async def test_retrieve_fetches_more_chunks_than_sections() -> None:
    asked: list[int] = []

    async def nearest(vector: Sequence[float], n: int) -> list[ChunkHit]:
        asked.append(n)
        # Four chunks of one section, then other sections.
        same = [hit("d", "Long", i, 0.95 - i * 0.01) for i in range(4)]
        return (same + [hit("d", f"S{i}", 10 + i, 0.5) for i in range(n)])[:n]

    hits = await retrieve([1.0], nearest, k=3, min_score=0.0)

    assert asked == [3 * CHUNK_FETCH_FACTOR]
    assert [h.heading_path for h in hits] == ["Long", "S0", "S1"]


async def test_k_must_be_positive() -> None:
    async def nearest(vector: Sequence[float], n: int) -> list[ChunkHit]:
        return []

    with pytest.raises(ValueError, match="k must be"):
        await retrieve([1.0], nearest, k=0, min_score=0.0)
