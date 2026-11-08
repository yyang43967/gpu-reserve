"""Text embeddings behind one small interface.

`OpenAIEmbedder` calls the embeddings API. `CachedEmbedder` keeps vectors on disk keyed
by model, dimensions, and text, so evaluation runs and parameter sweeps embed each text
once, and tests can replay recorded vectors without network access.
"""

import array
import base64
import gzip
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol

from openai import AsyncOpenAI

from app.core.config import Settings

# Inputs per API request. The API accepts more; smaller batches keep each request and
# its retry cheap.
EMBEDDING_BATCH_SIZE: Final = 256
EMBEDDING_MAX_RETRIES: Final = 5
EMBEDDING_TIMEOUT_SECONDS: Final = 30.0


class Embedder(Protocol):
    model: str
    dimensions: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbeddingError(RuntimeError):
    """Embeddings could not be produced."""


class CacheMissError(EmbeddingError):
    """A replaying embedder was asked for a text it has no recorded vector for."""


def _check(vectors: list[list[float]], expected: int, dimensions: int) -> list[list[float]]:
    if len(vectors) != expected:
        raise EmbeddingError(f"expected {expected} embeddings, got {len(vectors)}")
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError(f"expected {dimensions} dimensions, got {len(vector)}")
    return vectors


class OpenAIEmbedder:
    def __init__(self, client: AsyncOpenAI, model: str, dimensions: int) -> None:
        self._client = client
        self.model = model
        self.dimensions = dimensions
        # Tokens billed so far, from the API's usage field.
        self.tokens_used = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = list(texts[start : start + EMBEDDING_BATCH_SIZE])
            # The client retries rate limits, timeouts, and server errors with backoff.
            response = await self._client.embeddings.create(
                model=self.model, input=batch, dimensions=self.dimensions
            )
            self.tokens_used += response.usage.total_tokens
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(
                _check([item.embedding for item in ordered], len(batch), self.dimensions)
            )
        return vectors


class UnavailableEmbedder:
    """Stands in when no API key is configured. Knows the model, so hashes can still be
    computed for a dry run, and refuses to embed."""

    def __init__(self, model: str, dimensions: int) -> None:
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingError("OPENAI_API_KEY is not set, so nothing can be embedded")


def build_embedder(settings: Settings) -> OpenAIEmbedder | None:
    """The configured embedder, or None when no API key is set."""
    if settings.openai_api_key is None:
        return None
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        max_retries=EMBEDDING_MAX_RETRIES,
        timeout=EMBEDDING_TIMEOUT_SECONDS,
    )
    return OpenAIEmbedder(client, settings.embedding_model, settings.embedding_dimensions)


def cache_key(model: str, dimensions: int, text: str) -> str:
    return hashlib.sha256(f"{model}\0{dimensions}\0{text}".encode()).hexdigest()


def _encode(vector: list[float]) -> str:
    packed = array.array("f", vector)
    if sys.byteorder == "big":
        packed.byteswap()
    return base64.b64encode(packed.tobytes()).decode("ascii")


def _decode(data: str) -> list[float]:
    packed = array.array("f")
    packed.frombytes(base64.b64decode(data))
    if sys.byteorder == "big":
        packed.byteswap()
    return packed.tolist()


class CachedEmbedder:
    """Serves vectors from a cache and asks `inner` only for texts it has not seen.

    With no inner embedder it only replays, and a missing text raises CacheMissError.
    Vectors are stored as little-endian float32, which is what the API's floats
    round-trip to with no loss that matters for cosine similarity.
    """

    def __init__(
        self,
        model: str,
        dimensions: int,
        inner: Embedder | None = None,
        vectors: dict[str, list[float]] | None = None,
    ) -> None:
        if inner is not None and (inner.model, inner.dimensions) != (model, dimensions):
            raise ValueError("the inner embedder uses a different model or dimensions")
        self.model = model
        self.dimensions = dimensions
        self._inner = inner
        self.vectors: dict[str, list[float]] = vectors or {}
        self.misses = 0

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        keys = [cache_key(self.model, self.dimensions, text) for text in texts]
        missing = list(dict.fromkeys(k for k in keys if k not in self.vectors))
        if missing:
            if self._inner is None:
                raise CacheMissError(
                    f"{len(missing)} texts have no recorded embedding; the knowledge base or "
                    "the questions changed. Re-record with `python -m app.rag.evaluation record`."
                )
            by_key = dict(zip(keys, texts, strict=True))
            fresh = await self._inner.embed([by_key[k] for k in missing])
            self.vectors.update(zip(missing, fresh, strict=True))
            self.misses += len(missing)
        return _check([self.vectors[k] for k in keys], len(texts), self.dimensions)

    @classmethod
    def load(cls, path: Path, inner: Embedder | None = None) -> "CachedEmbedder":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            data = json.load(handle)
        vectors = {key: _decode(value) for key, value in data["vectors"].items()}
        return cls(data["model"], data["dimensions"], inner, vectors)

    def save(self, path: Path, keep: set[str] | None = None) -> None:
        """Write the cache, optionally only the given keys, in a stable order."""
        keys = sorted(self.vectors if keep is None else keep & self.vectors.keys())
        data = {
            "model": self.model,
            "dimensions": self.dimensions,
            "vectors": {key: _encode(self.vectors[key]) for key in keys},
        }
        # No name or mtime in the header keeps the file identical when the vectors are.
        with (
            path.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as zipped,
        ):
            zipped.write(json.dumps(data, indent=0, sort_keys=True).encode("utf-8"))
