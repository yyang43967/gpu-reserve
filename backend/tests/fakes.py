"""In-memory stand-ins for external services."""

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
