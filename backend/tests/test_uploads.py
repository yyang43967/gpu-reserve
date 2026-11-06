import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import InvalidUpload, PermissionDenied, UploadNotFound
from app.models import User, UserRole
from app.services.uploads import (
    MAX_UPLOAD_BYTES,
    MIB,
    UploadPurpose,
    create_upload,
    verify_upload,
)
from tests.fakes import FakeStorage

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _user(role: UserRole = UserRole.STUDENT) -> User:
    return User(id=uuid.uuid4(), email="ada@example.edu", full_name="Ada", role=role)


def _upload(
    storage: FakeStorage,
    user: User,
    purpose: UploadPurpose = UploadPurpose.AVATAR,
    content_type: str = "image/png",
    size_bytes: int = 1000,
) -> str:
    ticket = create_upload(
        storage,
        user,
        purpose=purpose,
        content_type=content_type,
        size_bytes=size_bytes,
        expires_in=300,
        now=NOW,
    )
    return ticket.key


def test_purpose_limits() -> None:
    assert MAX_UPLOAD_BYTES == {UploadPurpose.AVATAR: 2 * MIB, UploadPurpose.SERVER_IMAGE: 5 * MIB}


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [("image/png", "png"), ("image/jpeg", "jpg"), ("image/webp", "webp")],
)
def test_avatar_keys_are_scoped_to_the_user(content_type: str, extension: str) -> None:
    storage, user = FakeStorage(), _user()

    key = _upload(storage, user, content_type=content_type)

    assert re.fullmatch(rf"avatars/{user.id}/[0-9a-f]{{32}}\.{extension}", key)


def test_every_upload_gets_a_new_key() -> None:
    storage, user = FakeStorage(), _user()

    assert _upload(storage, user) != _upload(storage, user)


def test_ticket_carries_the_policy_and_expiry() -> None:
    storage, admin = FakeStorage(), _user(UserRole.ADMIN)

    ticket = create_upload(
        storage,
        admin,
        purpose=UploadPurpose.SERVER_IMAGE,
        content_type="image/webp",
        size_bytes=4 * MIB,
        expires_in=300,
        now=NOW,
    )

    assert re.fullmatch(r"servers/[0-9a-f]{32}\.webp", ticket.key)
    assert ticket.max_bytes == 5 * MIB
    assert ticket.expires_at == NOW + timedelta(seconds=300)
    assert storage.signed == [
        {
            "key": ticket.key,
            "content_type": "image/webp",
            "max_bytes": 5 * MIB,
            "expires_in": 300,
        }
    ]


def test_students_cannot_upload_server_images() -> None:
    with pytest.raises(PermissionDenied):
        _upload(FakeStorage(), _user(), UploadPurpose.SERVER_IMAGE)


@pytest.mark.parametrize("content_type", ["image/svg+xml", "image/gif", "text/html", "IMAGE/PNG"])
def test_only_allowed_types_are_signed(content_type: str) -> None:
    storage = FakeStorage()

    with pytest.raises(InvalidUpload):
        _upload(storage, _user(), content_type=content_type)
    assert storage.signed == []


@pytest.mark.parametrize(
    ("purpose", "role"),
    [(UploadPurpose.AVATAR, UserRole.STUDENT), (UploadPurpose.SERVER_IMAGE, UserRole.ADMIN)],
)
def test_declared_size_is_checked_per_purpose(purpose: UploadPurpose, role: UserRole) -> None:
    storage, user = FakeStorage(), _user(role)
    limit = MAX_UPLOAD_BYTES[purpose]

    _upload(storage, user, purpose, size_bytes=limit)
    with pytest.raises(InvalidUpload):
        _upload(storage, user, purpose, size_bytes=limit + 1)


# --- verify_upload ---


async def test_verify_accepts_a_good_upload() -> None:
    storage, user = FakeStorage(), _user()
    key = _upload(storage, user)
    storage.put(key, 1000, "image/png")

    await verify_upload(storage, key, purpose=UploadPurpose.AVATAR, owner=user)

    assert storage.deleted == []


@pytest.mark.parametrize(
    "key",
    [
        "avatars/{other}/abc.png",
        "servers/abc.png",
        "avatars/{me}/",
        "avatars/{me}/nested/abc.png",
        "avatars/{me}/../{other}/abc.png",
        "abc.png",
    ],
)
async def test_verify_rejects_keys_outside_the_owners_prefix(key: str) -> None:
    storage, user = FakeStorage(), _user()
    key = key.format(me=user.id, other=uuid.uuid4())
    storage.put(key, 1000, "image/png")

    with pytest.raises(InvalidUpload):
        await verify_upload(storage, key, purpose=UploadPurpose.AVATAR, owner=user)
    assert storage.deleted == []


async def test_server_images_live_under_servers() -> None:
    storage, admin = FakeStorage(), _user(UserRole.ADMIN)
    storage.put(f"avatars/{admin.id}/abc.png", 1000, "image/png")

    with pytest.raises(InvalidUpload):
        await verify_upload(
            storage,
            f"avatars/{admin.id}/abc.png",
            purpose=UploadPurpose.SERVER_IMAGE,
            owner=admin,
        )


async def test_verify_reports_a_missing_object() -> None:
    storage, user = FakeStorage(), _user()

    with pytest.raises(UploadNotFound):
        await verify_upload(
            storage, f"avatars/{user.id}/abc.png", purpose=UploadPurpose.AVATAR, owner=user
        )


@pytest.mark.parametrize(
    ("size_bytes", "content_type"),
    [
        (2 * MIB + 1, "image/png"),
        (0, "image/png"),
        (1000, "text/html"),
        (1000, "image/svg+xml"),
        (1000, ""),
    ],
)
async def test_verify_deletes_bad_objects(size_bytes: int, content_type: str) -> None:
    storage, user = FakeStorage(), _user()
    key = f"avatars/{user.id}/abc.png"
    storage.put(key, size_bytes, content_type)

    with pytest.raises(InvalidUpload):
        await verify_upload(storage, key, purpose=UploadPurpose.AVATAR, owner=user)
    assert storage.deleted == [key]


async def test_server_images_may_be_larger_than_avatars() -> None:
    storage, admin = FakeStorage(), _user(UserRole.ADMIN)
    key = "servers/abc.png"
    storage.put(key, 4 * MIB, "image/png")

    await verify_upload(storage, key, purpose=UploadPurpose.SERVER_IMAGE, owner=admin)
