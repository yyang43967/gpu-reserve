import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.models import User, UserRole

if TYPE_CHECKING:
    from app.services.storage import ObjectStorage


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    weekly_gpu_hour_quota: int
    is_active: bool
    avatar_key: str | None
    # A presigned download URL, or None without an avatar.
    avatar_url: str | None = None
    created_at: datetime

    @classmethod
    def build(cls, user: User, storage: "ObjectStorage", now: datetime) -> "UserRead":
        url = storage.download_url(user.avatar_key, now=now) if user.avatar_key else None
        return cls.model_validate(user).model_copy(update={"avatar_url": url})
