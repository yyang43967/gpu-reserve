from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.rag import embeddings
from app.rag.embeddings import (
    CachedEmbedder,
    CacheMissError,
    EmbeddingError,
    OpenAIEmbedder,
    build_embedder,
    cache_key,
)
from tests.fakes import FakeEmbedder


@dataclass
class _Item:
    index: int
    embedding: list[float]


@dataclass
class _Usage:
    total_tokens: int


@dataclass
class _Response:
    data: list[_Item]
    usage: _Usage


class _FakeEmbeddingsApi:
    def __init__(self, width: int) -> None:
        self.width = width
        self.requests: list[dict[str, Any]] = []

    async def create(self, *, model: str, input: list[str], dimensions: int) -> _Response:
        self.requests.append({"model": model, "input": input, "dimensions": dimensions})
        # Returned out of order, as the API allows; each vector encodes its text's length.
        items = [_Item(i, [float(len(text))] * self.width) for i, text in enumerate(input)]
        return _Response(list(reversed(items)), _Usage(total_tokens=len(input) * 3))


class _FakeClient:
    def __init__(self, width: int) -> None:
        self.embeddings = _FakeEmbeddingsApi(width)


def openai_embedder(width: int = 4, dimensions: int = 4) -> tuple[OpenAIEmbedder, _FakeClient]:
    client = _FakeClient(width)
    return OpenAIEmbedder(client, "text-embedding-3-small", dimensions), client  # type: ignore[arg-type]


async def test_openai_embedder_batches_orders_and_counts_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embeddings, "EMBEDDING_BATCH_SIZE", 2)
    embedder, client = openai_embedder()

    vectors = await embedder.embed(["a", "bb", "ccc"])

    assert [v[0] for v in vectors] == [1.0, 2.0, 3.0]
    assert [r["input"] for r in client.embeddings.requests] == [["a", "bb"], ["ccc"]]
    assert {r["dimensions"] for r in client.embeddings.requests} == {4}
    assert embedder.tokens_used == 9


async def test_openai_embedder_rejects_wrong_dimensions() -> None:
    embedder, _ = openai_embedder(width=3, dimensions=4)

    with pytest.raises(EmbeddingError, match="expected 4 dimensions"):
        await embedder.embed(["a"])


def test_no_api_key_means_no_embedder() -> None:
    assert build_embedder(Settings(openai_api_key="")) is None
    assert build_embedder(Settings(openai_api_key="sk-test")) is not None


def test_embedding_dimensions_must_match_the_column() -> None:
    with pytest.raises(ValidationError, match="width of the embedding column"):
        Settings(embedding_dimensions=3072)


async def test_cache_embeds_each_text_once() -> None:
    inner = FakeEmbedder(dimensions=8)
    cache = CachedEmbedder("fake-embedding", 8, inner)

    first = await cache.embed(["alpha", "beta", "alpha"])
    second = await cache.embed(["beta", "gamma"])

    assert inner.calls == [["alpha", "beta"], ["gamma"]]
    assert first[0] == first[2] == inner.vector("alpha")
    assert second[0] == first[1]
    assert cache.misses == 3


async def test_replay_only_cache_refuses_unknown_texts() -> None:
    cache = CachedEmbedder("fake-embedding", 8)

    with pytest.raises(CacheMissError, match="Re-record"):
        await cache.embed(["unseen"])


def test_cache_rejects_an_inner_embedder_for_another_model() -> None:
    with pytest.raises(ValueError, match="different model"):
        CachedEmbedder("other", 8, FakeEmbedder(dimensions=8))


async def test_cache_round_trips_through_a_file(tmp_path: Path) -> None:
    cache = CachedEmbedder("fake-embedding", 8, FakeEmbedder(dimensions=8))
    vectors = await cache.embed(["alpha", "beta"])
    path = tmp_path / "embeddings.json.gz"

    cache.save(path)
    loaded = CachedEmbedder.load(path)

    assert (loaded.model, loaded.dimensions) == ("fake-embedding", 8)
    replayed = await loaded.embed(["alpha", "beta"])
    for original, restored in zip(vectors, replayed, strict=True):
        assert restored == pytest.approx(original, abs=1e-6)


async def test_saved_file_is_byte_stable_and_can_be_pruned(tmp_path: Path) -> None:
    cache = CachedEmbedder("fake-embedding", 8, FakeEmbedder(dimensions=8))
    await cache.embed(["alpha", "beta"])
    first, second, pruned = tmp_path / "a.gz", tmp_path / "b.gz", tmp_path / "c.gz"

    cache.save(first)
    cache.save(second)
    cache.save(pruned, keep={cache_key("fake-embedding", 8, "alpha")})

    assert first.read_bytes() == second.read_bytes()
    assert set(CachedEmbedder.load(pruned).vectors) == {cache_key("fake-embedding", 8, "alpha")}
