from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import botocore.auth
import pytest

from app.core.config import Settings
from app.services.storage import S3Storage, signing_window_start

WINDOW_START = datetime(2026, 11, 6, 14, 0, tzinfo=UTC)


def _settings(**values: object) -> Settings:
    base: dict[str, object] = {
        "s3_endpoint_url": "http://localhost:8333",
        "s3_region": "us-east-1",
        "s3_access_key_id": "test-access-key",
        "s3_secret_access_key": "test-secret-key",
        "s3_bucket_uploads": "uploads",
    }
    base.update(values)
    return Settings.model_validate(base)


def _query(url: str) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}


# --- credentials ---


@pytest.mark.parametrize(
    "field",
    [
        "s3_endpoint_url",
        "s3_public_endpoint_url",
        "s3_region",
        "s3_access_key_id",
        "s3_secret_access_key",
    ],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_storage_settings_are_unset(field: str, blank: str) -> None:
    assert getattr(_settings(**{field: blank}), field) is None


def test_blank_keys_fall_back_to_the_default_credential_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With `S3_ACCESS_KEY_ID=` in the env file, boto3 must use its own chain (the
    environment here, the instance role in production), not an empty key."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-the-chain")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "chain-secret")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "")

    settings = Settings()
    storage = S3Storage(settings)

    assert settings.s3_access_key_id is None
    assert settings.s3_secret_access_key is None
    post = storage.presign_post(
        "servers/a.png", content_type="image/png", max_bytes=1, expires_in=60
    )
    assert post.fields["x-amz-credential"].startswith("from-the-chain/")


def test_configured_keys_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "from-the-chain")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "chain-secret")

    post = S3Storage(_settings()).presign_post(
        "servers/a.png", content_type="image/png", max_bytes=1, expires_in=60
    )

    assert post.fields["x-amz-credential"].startswith("test-access-key/")


# --- windowed download URLs ---


@pytest.mark.parametrize(
    ("now", "start"),
    [
        (datetime(2026, 11, 6, 14, 0, tzinfo=UTC), WINDOW_START),
        (datetime(2026, 11, 6, 14, 59, 59, 999999, tzinfo=UTC), WINDOW_START),
        # 06:15 in Los Angeles (UTC-8 in November) is 14:15 UTC.
        (datetime(2026, 11, 6, 6, 15, tzinfo=ZoneInfo("America/Los_Angeles")), WINDOW_START),
    ],
)
def test_window_start(now: datetime, start: datetime) -> None:
    assert signing_window_start(now) == start


def test_same_url_within_a_window() -> None:
    storage = S3Storage(_settings())

    first = storage.download_url("servers/a.png", now=WINDOW_START + timedelta(minutes=1))
    last = storage.download_url("servers/a.png", now=WINDOW_START + timedelta(minutes=59))

    assert first == last
    query = _query(first)
    assert query["X-Amz-Date"] == "20261106T140000Z"
    assert query["X-Amz-Expires"] == "7200"


def test_new_url_in_the_next_window() -> None:
    storage = S3Storage(_settings())

    this_hour = storage.download_url("servers/a.png", now=WINDOW_START)
    next_hour = storage.download_url("servers/a.png", now=WINDOW_START + timedelta(hours=1))

    assert this_hour != next_hour
    assert _query(next_hour)["X-Amz-Date"] == "20261106T150000Z"


def test_url_uses_the_public_endpoint() -> None:
    storage = S3Storage(
        _settings(
            s3_endpoint_url="http://seaweedfs:8333",
            s3_public_endpoint_url="http://localhost:8333",
        )
    )

    url = storage.download_url("servers/a.png", now=WINDOW_START)

    assert url.startswith("http://localhost:8333/uploads/servers/a.png?")


@pytest.mark.parametrize("key", ["servers/a.png", "avatars/1234/name with spaces+plus.png"])
def test_signature_matches_botocore_at_the_same_time(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """Cross-check against botocore's own presigner, with its clock pinned only here."""
    storage = S3Storage(_settings())
    monkeypatch.setattr(botocore.auth, "get_current_datetime", lambda: WINDOW_START)

    expected = storage._public.generate_presigned_url(
        "get_object", Params={"Bucket": "uploads", "Key": key}, ExpiresIn=7200
    )
    actual = storage.download_url(key, now=WINDOW_START + timedelta(minutes=17))

    assert actual == expected
