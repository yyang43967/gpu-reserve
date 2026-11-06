"""The upload flow against a real SeaweedFS, started with the same script as Compose.

Proves that storage itself enforces the presigned policy and refuses anonymous access,
and that verify_upload catches what a client stores outside the policy.
"""

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from testcontainers.core.container import DockerContainer

from app.core.config import Settings
from app.core.exceptions import InvalidUpload
from app.models import User, UserRole
from app.services.storage import S3Storage
from app.services.uploads import MIB, UploadPurpose, create_upload, verify_upload
from tests.integration.conftest import Environment

pytestmark = pytest.mark.integration

SEAWEEDFS_IMAGE = "chrislusf/seaweedfs:4.51"
START_SCRIPT = Path(__file__).resolve().parents[3] / "infra/local/seaweedfs/start.sh"
BUCKET = "test-uploads"
ACCESS_KEY = "test-app-access-key"
SECRET_KEY = "test-app-secret-key-0123456789"
FRONTEND_ORIGIN = "http://localhost:5173"
STARTUP_TIMEOUT_SECONDS = 60


@pytest.fixture(scope="module")
def seaweedfs_url() -> Iterator[str]:
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_exposed_ports(8333)
        .with_volume_mapping(str(START_SCRIPT), "/opt/gpu-reserve/start.sh", "ro")
        .with_envs(
            S3_ACCESS_KEY_ID=ACCESS_KEY,
            S3_SECRET_ACCESS_KEY=SECRET_KEY,
            S3_BUCKET_UPLOADS=BUCKET,
            S3_ALLOWED_ORIGINS=FRONTEND_ORIGIN,
            GOMEMLIMIT="160MiB",
        )
        .with_kwargs(entrypoint=["/bin/sh", "/opt/gpu-reserve/start.sh"], mem_limit="256m")
    )
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8333)}"
        _wait_for_bucket(url)
        yield url


def _wait_for_bucket(url: str) -> None:
    """The gateway answers before the bucket exists; wait until it is created."""
    storage = S3Storage(_settings(url))
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            storage._internal.head_bucket(Bucket=BUCKET)
            return
        except Exception:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _settings(url: str) -> Settings:
    return Settings.model_validate(
        {
            "environment": Environment.TEST,
            "s3_endpoint_url": url,
            "s3_region": "us-east-1",
            "s3_access_key_id": ACCESS_KEY,
            "s3_secret_access_key": SECRET_KEY,
            "s3_bucket_uploads": BUCKET,
        }
    )


@pytest.fixture
def storage(seaweedfs_url: str) -> S3Storage:
    return S3Storage(_settings(seaweedfs_url))


def _user(role: UserRole = UserRole.STUDENT) -> User:
    return User(id=uuid.uuid4(), email="ada@example.edu", full_name="Ada", role=role)


def _post(
    storage: S3Storage,
    user: User,
    purpose: UploadPurpose,
    body: bytes,
    *,
    content_type: str = "image/png",
    sent_type: str | None = None,
    declared_size: int | None = None,
) -> tuple[str, httpx.Response]:
    """Sign an upload and send `body`. `declared_size` lets a test lie about the size."""
    ticket = create_upload(
        storage,
        user,
        purpose=purpose,
        content_type=content_type,
        size_bytes=declared_size or max(len(body), 1),
        expires_in=60,
        now=datetime.now(UTC),
    )
    fields = dict(ticket.fields)
    if sent_type is not None:
        fields["Content-Type"] = sent_type
    response = httpx.post(
        ticket.url,
        data=fields,
        files={"file": ("upload", body, sent_type or content_type)},
        timeout=30,
    )
    return ticket.key, response


async def test_upload_then_verify(storage: S3Storage) -> None:
    user = _user()

    key, response = _post(storage, user, UploadPurpose.AVATAR, b"\x89PNG" + b"0" * 1000)

    assert response.status_code == 204, response.text
    await verify_upload(storage, key, purpose=UploadPurpose.AVATAR, owner=user)
    info = await storage.head(key)
    assert info is not None
    assert (info.size_bytes, info.content_type) == (1004, "image/png")


@pytest.mark.parametrize(
    ("purpose", "role"),
    [(UploadPurpose.AVATAR, UserRole.STUDENT), (UploadPurpose.SERVER_IMAGE, UserRole.ADMIN)],
)
async def test_storage_rejects_files_over_the_purpose_limit(
    storage: S3Storage, purpose: UploadPurpose, role: UserRole
) -> None:
    limit = 2 * MIB if purpose is UploadPurpose.AVATAR else 5 * MIB

    # The client declares a small file, then sends a larger one.
    key, response = _post(storage, _user(role), purpose, b"0" * (limit + 1), declared_size=1000)

    assert response.status_code == 400, response.text
    assert await storage.head(key) is None


async def test_storage_rejects_a_different_content_type(storage: S3Storage) -> None:
    key, response = _post(
        storage, _user(), UploadPurpose.AVATAR, b"<svg/>", sent_type="image/svg+xml"
    )

    assert response.status_code in (400, 403), response.text
    assert await storage.head(key) is None


async def test_verify_deletes_objects_stored_outside_the_policy(storage: S3Storage) -> None:
    """An object written by other means, such as a leaked key, is still caught."""
    user = _user()
    key = f"avatars/{user.id}/{uuid.uuid4().hex}.png"
    storage._internal.put_object(
        Bucket=BUCKET, Key=key, Body=b"0" * (2 * MIB + 1), ContentType="image/png"
    )

    with pytest.raises(InvalidUpload, match="larger than 2 MiB"):
        await verify_upload(storage, key, purpose=UploadPurpose.AVATAR, owner=user)
    assert await storage.head(key) is None


async def test_missing_object_and_delete(storage: S3Storage) -> None:
    assert await storage.head(f"avatars/{uuid.uuid4()}/missing.png") is None
    await storage.delete(f"avatars/{uuid.uuid4()}/missing.png")


def test_anonymous_requests_are_rejected(seaweedfs_url: str, storage: S3Storage) -> None:
    key = f"servers/{uuid.uuid4().hex}.png"
    storage._internal.put_object(Bucket=BUCKET, Key=key, Body=b"png", ContentType="image/png")

    get = httpx.get(f"{seaweedfs_url}/{BUCKET}/{key}")
    put = httpx.put(f"{seaweedfs_url}/{BUCKET}/servers/anonymous.png", content=b"x")
    listing = httpx.get(f"{seaweedfs_url}/{BUCKET}")

    assert (get.status_code, put.status_code, listing.status_code) == (403, 403, 403)


def test_cors_preflight_allows_only_the_frontend(seaweedfs_url: str) -> None:
    def preflight(origin: str) -> httpx.Response:
        return httpx.options(
            f"{seaweedfs_url}/{BUCKET}",
            headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
        )

    assert preflight(FRONTEND_ORIGIN).headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    assert "access-control-allow-origin" not in preflight("http://evil.example").headers
