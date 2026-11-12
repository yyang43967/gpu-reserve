import uuid
from enum import StrEnum
from typing import Final

from sqlalchemy import CheckConstraint, Enum, String, Text, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

DEFAULT_WEEKLY_GPU_HOUR_QUOTA: Final = 48
EMAIL_MAX_LENGTH: Final = 320
FULL_NAME_MAX_LENGTH: Final = 200
OBJECT_KEY_MAX_LENGTH: Final = 512


class UserRole(StrEnum):
    STUDENT = "student"
    ADMIN = "admin"


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("weekly_gpu_hour_quota >= 0", name="quota_non_negative"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    # Stored lowercased; the service layer normalizes before every read and write.
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(FULL_NAME_MAX_LENGTH))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda roles: [r.value for r in roles]),
        default=UserRole.STUDENT,
        server_default=UserRole.STUDENT.value,
    )
    weekly_gpu_hour_quota: Mapped[int] = mapped_column(
        default=DEFAULT_WEEKLY_GPU_HOUR_QUOTA,
        server_default=text(str(DEFAULT_WEEKLY_GPU_HOUR_QUOTA)),
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    # The public demo account, shared by every visitor (set by the demo seed). It cannot
    # change its profile or cancel the bookings it was seeded with, and a daily job
    # removes what visitors added.
    is_demo: Mapped[bool] = mapped_column(default=False, server_default=false())
    avatar_key: Mapped[str | None] = mapped_column(String(OBJECT_KEY_MAX_LENGTH))
