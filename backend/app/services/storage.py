"""Object storage behind presigned URLs.

Browsers upload straight to storage with a presigned POST; the API only signs requests
and checks objects. Signing is local computation. HEAD and DELETE are network calls and
run in a worker thread so they do not block the event loop.
"""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


@dataclass(frozen=True)
class PresignedPost:
    url: str
    fields: dict[str, str]


@dataclass(frozen=True)
class ObjectInfo:
    size_bytes: int
    content_type: str


class ObjectStorage(Protocol):
    def presign_post(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedPost: ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def delete(self, key: str) -> None: ...


def _client(settings: Settings, endpoint_url: str | None) -> "S3Client":
    secret = settings.s3_secret_access_key
    client: S3Client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return client


class S3Storage:
    """S3 or an S3-compatible server.

    Two clients: one signs URLs for the address browsers use, the other makes the API's
    own calls over the internal address. In production both are AWS S3.
    """

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket_uploads
        self._internal = _client(settings, settings.s3_endpoint_url)
        self._public = _client(
            settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url
        )

    def presign_post(
        self, key: str, *, content_type: str, max_bytes: int, expires_in: int
    ) -> PresignedPost:
        """A form upload of exactly this key, this content type, and 1..max_bytes bytes.

        Storage enforces the policy: anything else is rejected and nothing is stored.
        """
        conditions: list[Any] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
        ]
        post = self._public.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=conditions,
            ExpiresIn=expires_in,
        )
        return PresignedPost(url=post["url"], fields=dict(post["fields"]))

    async def head(self, key: str) -> ObjectInfo | None:
        try:
            response = await asyncio.to_thread(
                self._internal.head_object, Bucket=self._bucket, Key=key
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                return None
            raise
        return ObjectInfo(
            size_bytes=response["ContentLength"], content_type=response.get("ContentType", "")
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._internal.delete_object, Bucket=self._bucket, Key=key)
