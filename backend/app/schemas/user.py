import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import UserRole


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    weekly_gpu_hour_quota: int
    is_active: bool
    avatar_key: str | None
    created_at: datetime
