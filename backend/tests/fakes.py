"""In-memory stand-ins for external services."""

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.services.storage import ObjectInfo, PresignedPost, signing_window_start


@dataclass
class FakeStorage:
    """Signs nothing and stores object metadata in a dict."""

    objects: dict[str, ObjectInfo] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    signed: list[dict[str, object]] = field(default_factory=list)

    def presign_post(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedPost:
        self.signed.append(
            {
                "key": key,
                "content_type": content_type,
                "max_bytes": max_bytes,
                "expires_in": expires_in,
            }
        )
        return PresignedPost(
            url="http://storage.test/uploads",
            fields={"key": key, "Content-Type": content_type, "policy": "signed"},
        )

    def download_url(self, key: str, *, now: datetime) -> str:
        return (
            f"http://storage.test/uploads/{key}?signed={signing_window_start(now):%Y%m%dT%H%M%SZ}"
        )

    async def head(self, key: str) -> ObjectInfo | None:
        return self.objects.get(key)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    def put(self, key: str, size_bytes: int, content_type: str) -> None:
        self.objects[key] = ObjectInfo(size_bytes=size_bytes, content_type=content_type)


class FakeEmbedder:
    """Deterministic embeddings with no network: a hashed bag of words, normalized.

    Texts that share words get similar vectors, so retrieval order and score thresholds
    can be tested meaningfully. `calls` records every batch it was asked to embed.
    """

    def __init__(self, model: str = "fake-embedding", dimensions: int = 1536) -> None:
        self.model = model
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    def vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0:
            # pgvector has no cosine distance for a zero vector.
            values[0], norm = 1.0, 1.0
        return [v / norm for v in values]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vector(text) for text in texts]

    @property
    def embedded(self) -> int:
        return sum(len(batch) for batch in self.calls)
