"""Object storage behind presigned URLs.

Browsers upload straight to storage with a presigned POST and download with presigned
GETs; the API only signs requests and checks objects. Signing is local computation.
HEAD and DELETE are network calls and run in a worker thread so they do not block the
event loop.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol
from urllib.parse import quote

import boto3
from botocore.auth import SIGV4_TIMESTAMP, S3SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import ReadOnlyCredentials
from botocore.exceptions import ClientError, NoCredentialsError

from app.core.config import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})

# Download URLs are signed as of the start of the current hour and stay valid for two
# hours. Every request within the hour gets the same URL, so browsers can cache the
# image, and every URL works for at least an hour after it is handed out.
DOWNLOAD_URL_WINDOW: Final = timedelta(hours=1)
DOWNLOAD_URL_TTL: Final = timedelta(hours=2)


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

    def download_url(self, key: str, *, now: datetime) -> str: ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def delete(self, key: str) -> None: ...


def signing_window_start(now: datetime) -> datetime:
    """The start of the window `now` falls in, in UTC."""
    window = int(DOWNLOAD_URL_WINDOW.total_seconds())
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window, tz=UTC)


class _FixedTimeS3QueryAuth(S3SigV4QueryAuth):
    """botocore's presigned-URL signer with the signing time passed in.

    The stock `add_auth` reads the clock itself. This version takes the time from the
    instance, so each signature is independent and nothing global is patched. The steps
    after setting the time are the same as botocore's.
    """

    def __init__(
        self,
        credentials: ReadOnlyCredentials,
        region_name: str,
        expires: int,
        signed_at: datetime,
    ) -> None:
        super().__init__(credentials, "s3", region_name, expires=expires)
        self._signed_at = signed_at

    def add_auth(self, request: AWSRequest) -> None:
        request.context["timestamp"] = self._signed_at.strftime(SIGV4_TIMESTAMP)
        # The type stubs omit these two hooks; both are defined on SigV4QueryAuth.
        self._modify_request_before_signing(request)  # type: ignore[attr-defined]
        canonical_request = self.canonical_request(request)
        string_to_sign = self.string_to_sign(request, canonical_request)
        signature = self.signature(string_to_sign, request)
        self._inject_signature_to_request(request, signature)  # type: ignore[attr-defined]


def _session(settings: Settings) -> boto3.Session:
    """Explicit keys when configured; otherwise boto3's default chain (environment,
    shared config, instance role)."""
    secret = settings.s3_secret_access_key
    return boto3.Session(
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=secret.get_secret_value() if secret else None,
        region_name=settings.s3_region,
    )


def _client(session: boto3.Session, endpoint_url: str | None) -> "S3Client":
    client: S3Client = session.client(
        "s3",
        endpoint_url=endpoint_url,
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
        self._session = _session(settings)
        self._internal = _client(self._session, settings.s3_endpoint_url)
        self._public = _client(
            self._session, settings.s3_public_endpoint_url or settings.s3_endpoint_url
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

    def download_url(self, key: str, *, now: datetime) -> str:
        """A presigned GET for `key`, signed at the start of the current window."""
        credentials = self._session.get_credentials()
        if credentials is None:
            raise NoCredentialsError()
        url = f"{self._public.meta.endpoint_url}/{self._bucket}/{quote(key, safe='/~')}"
        request = AWSRequest(method="GET", url=url)
        _FixedTimeS3QueryAuth(
            credentials.get_frozen_credentials(),
            self._public.meta.region_name,
            expires=int(DOWNLOAD_URL_TTL.total_seconds()),
            signed_at=signing_window_start(now),
        ).add_auth(request)
        if request.url is None:  # pragma: no cover - set above, typed as optional
            raise RuntimeError("Signing produced no URL.")
        return request.url

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
