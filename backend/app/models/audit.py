import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

AUDIT_ACTION_MAX_LENGTH: Final = 64
AUDIT_ENTITY_TYPE_MAX_LENGTH: Final = 64


class AuditLog(Base):
    """An append-only record of who changed what.

    `actor_id` has no foreign key on purpose: the history must outlive the account.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    actor_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(AUDIT_ACTION_MAX_LENGTH))
    entity_type: Mapped[str] = mapped_column(String(AUDIT_ENTITY_TYPE_MAX_LENGTH))
    entity_id: Mapped[uuid.UUID]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
