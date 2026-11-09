import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

THREAD_TITLE_MAX_LENGTH: Final = 120
TOOL_CALL_ID_MAX_LENGTH: Final = 128
ASSISTANT_ACTION_MAX_LENGTH: Final = 64


def _enum_values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ActionOutcome(StrEnum):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    # Replaced by a new message before the user confirmed or cancelled.
    SUPERSEDED = "superseded"


class ChatThread(TimestampMixin, Base):
    """A conversation with the assistant. The agent's own state lives in the LangGraph
    checkpointer under the same id; this row holds what the API needs around it."""

    __tablename__ = "chat_threads"
    __table_args__ = (Index("ix_chat_threads_user_id_updated_at", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(THREAD_TITLE_MAX_LENGTH))
    # The confirmation the user has been asked for: {"tool_call_id", "payload"}. Lets the
    # page show the card again after a reload and lets one /resume claim it atomically.
    pending_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # While a run holds the thread. Claimed with a conditional UPDATE, renewed during
    # the run, cleared at the end; a crashed run's lease simply expires.
    run_lease_until: Mapped[datetime | None]


class ChatMessage(Base):
    """What the page shows: the user's messages and the assistant's answers, with the
    answer's citations and tool steps in `meta`."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread_id_created_at", "thread_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    role: Mapped[ChatRole] = mapped_column(
        Enum(ChatRole, name="chat_role", values_callable=_enum_values)
    )
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class AssistantAction(Base):
    """How one write-gated tool call was resolved. The unique key makes the outcome,
    and so the write, happen at most once per tool call: a confirmed row commits or
    rolls back with the reservation it describes, and a resumed tool that finds its
    row returns the stored result instead of acting again."""

    __tablename__ = "assistant_actions"
    __table_args__ = (
        UniqueConstraint("thread_id", "tool_call_id", name="uq_assistant_actions_thread_call"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_threads.id", ondelete="CASCADE"))
    tool_call_id: Mapped[str] = mapped_column(String(TOOL_CALL_ID_MAX_LENGTH))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(ASSISTANT_ACTION_MAX_LENGTH))
    outcome: Mapped[ActionOutcome] = mapped_column(
        Enum(ActionOutcome, name="assistant_action_outcome", values_callable=_enum_values)
    )
    # The tool result returned to the model, replayed if the call is resumed again.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
