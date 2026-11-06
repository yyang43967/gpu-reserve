"""Upload rules: who may upload what, where it goes, and checking it before use.

Keys are generated here, never chosen by clients, and are scoped by purpose and owner.
A key is only saved on a record after `verify_upload` has confirmed the object exists,
is small enough, and has an allowed type.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from app.core.exceptions import InvalidUpload, PermissionDenied, UploadNotFound
from app.models import User, UserRole
from app.services.storage import ObjectStorage

logger = logging.getLogger(__name__)

MIB: Final = 1024 * 1024


class UploadPurpose(StrEnum):
    AVATAR = "avatar"
    SERVER_IMAGE = "server_image"


MAX_UPLOAD_BYTES: Final[dict[UploadPurpose, int]] = {
    UploadPurpose.AVATAR: 2 * MIB,
    UploadPurpose.SERVER_IMAGE: 5 * MIB,
}

# Allowed content types and the file extension used in the key.
ALLOWED_CONTENT_TYPES: Final[dict[str, str]] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}

AVATAR_PREFIX: Final = "avatars"
SERVER_IMAGE_PREFIX: Final = "servers"


def key_prefix(purpose: UploadPurpose, owner: User) -> str:
    """Where objects for this purpose and owner live, with a trailing slash."""
    if purpose is UploadPurpose.AVATAR:
        return f"{AVATAR_PREFIX}/{owner.id}/"
    return f"{SERVER_IMAGE_PREFIX}/"


@dataclass(frozen=True)
class UploadTicket:
    key: str
    url: str
    fields: dict[str, str]
    max_bytes: int
    expires_at: datetime


def create_upload(
    storage: ObjectStorage,
    user: User,
    *,
    purpose: UploadPurpose,
    content_type: str,
    size_bytes: int,
    expires_in: int,
    now: datetime,
) -> UploadTicket:
    """Sign an upload of one image to a fresh key.

    Any signed-in user may upload an avatar; only admins may upload server images.
    The declared size is checked here for a fast answer; storage enforces the real one.
    """
    if purpose is UploadPurpose.SERVER_IMAGE and user.role is not UserRole.ADMIN:
        raise PermissionDenied("Only administrators can upload server images.")
    extension = ALLOWED_CONTENT_TYPES.get(content_type)
    if extension is None:
        allowed = ", ".join(ALLOWED_CONTENT_TYPES)
        raise InvalidUpload(f"Upload a PNG, JPEG, or WebP image ({allowed}).")
    max_bytes = MAX_UPLOAD_BYTES[purpose]
    if size_bytes > max_bytes:
        raise InvalidUpload(f"The file is larger than {max_bytes // MIB} MiB.")

    key = f"{key_prefix(purpose, user)}{uuid.uuid4().hex}.{extension}"
    post = storage.presign_post(
        key, content_type=content_type, max_bytes=max_bytes, expires_in=expires_in
    )
    return UploadTicket(
        key=key,
        url=post.url,
        fields=post.fields,
        max_bytes=max_bytes,
        expires_at=now + timedelta(seconds=expires_in),
    )


async def verify_upload(
    storage: ObjectStorage, key: str, *, purpose: UploadPurpose, owner: User
) -> None:
    """Check an uploaded object before its key is saved on a record.

    Raises InvalidUpload for a key outside the owner's prefix for this purpose, and
    UploadNotFound when nothing was uploaded there. An object that is too large or has
    a type outside the allowlist is deleted and reported as InvalidUpload. This does not
    rely on the storage server having enforced the upload policy.
    """
    prefix = key_prefix(purpose, owner)
    name = key.removeprefix(prefix)
    if not key.startswith(prefix) or not name or "/" in name or ".." in key:
        raise InvalidUpload("That upload does not belong here.")

    info = await storage.head(key)
    if info is None:
        raise UploadNotFound("Nothing was uploaded under that key. Upload the file first.")

    problem = None
    if info.size_bytes > MAX_UPLOAD_BYTES[purpose]:
        problem = f"The file is larger than {MAX_UPLOAD_BYTES[purpose] // MIB} MiB."
    elif info.size_bytes == 0:
        problem = "The file is empty."
    elif info.content_type not in ALLOWED_CONTENT_TYPES:
        problem = "Upload a PNG, JPEG, or WebP image."
    if problem is not None:
        logger.warning("Rejected upload %s: %s", key, problem)
        await storage.delete(key)
        raise InvalidUpload(problem)


async def delete_quietly(storage: ObjectStorage, key: str) -> None:
    """Delete an object that a committed change no longer refers to.

    Runs after the response, so a failure is logged and the object is left behind
    rather than failing a change that already succeeded.
    """
    try:
        await storage.delete(key)
    except Exception:
        logger.exception("Could not delete replaced upload %s", key)
