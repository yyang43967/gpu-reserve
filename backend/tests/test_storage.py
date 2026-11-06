from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.storage import S3Storage


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
