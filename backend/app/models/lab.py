import uuid
from typing import Final

from sqlalchemy import Index, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

DEFAULT_LAB_TIMEZONE: Final = "America/Los_Angeles"
LAB_NAME_MAX_LENGTH: Final = 100
LAB_LOCATION_MAX_LENGTH: Final = 200
# Longest IANA zone names are around 30 characters.
TIMEZONE_MAX_LENGTH: Final = 64


class Lab(TimestampMixin, Base):
    __tablename__ = "labs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(String(LAB_NAME_MAX_LENGTH))
    location: Mapped[str] = mapped_column(String(LAB_LOCATION_MAX_LENGTH))
    # IANA zone name, validated by the API schema.
    timezone: Mapped[str] = mapped_column(
        String(TIMEZONE_MAX_LENGTH),
        default=DEFAULT_LAB_TIMEZONE,
        server_default=DEFAULT_LAB_TIMEZONE,
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())


# Names are unique regardless of case, so "Vision Lab" and "vision lab" cannot both
# exist. The stored name keeps the case the admin typed.
Index("uq_labs_name_lower", func.lower(Lab.name), unique=True)
