from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import UserRole
from app.models.user import DEFAULT_WEEKLY_GPU_HOUR_QUOTA
from app.schemas.auth import FullName, NewPassword

# A sanity bound that keeps values far from the integer column's limit. A week has
# 168 hours, so this allows dozens of GPUs around the clock.
MAX_WEEKLY_GPU_HOUR_QUOTA: Final = 10_000

Quota = Annotated[int, Field(ge=0, le=MAX_WEEKLY_GPU_HOUR_QUOTA)]


class AdminUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: FullName
    # Required: admins set an explicit initial password, never a generated default.
    password: NewPassword
    role: UserRole = UserRole.STUDENT
    weekly_gpu_hour_quota: Quota = DEFAULT_WEEKLY_GPU_HOUR_QUOTA


class AdminUserUpdate(BaseModel):
    """Omitted fields are left unchanged."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole | None = None
    is_active: bool | None = None
    weekly_gpu_hour_quota: Quota | None = None
