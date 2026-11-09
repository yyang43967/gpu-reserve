import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.models import ChatMessage, ChatRole, ChatThread
from app.models.chat import THREAD_TITLE_MAX_LENGTH

# Longest message accepted from the user. Longer input costs tokens without helping a
# booking assistant, and is a common vehicle for prompt injection.
MESSAGE_MAX_LENGTH: Final = 2000


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=THREAD_TITLE_MAX_LENGTH)]
        | None
    ) = None


class ThreadRead(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    has_pending_action: bool

    @classmethod
    def build(cls, thread: ChatThread) -> Self:
        return cls(
            id=thread.id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            has_pending_action=thread.pending_action is not None,
        )


class MessageRead(BaseModel):
    id: uuid.UUID
    role: ChatRole
    content: str
    meta: dict[str, Any]
    created_at: datetime

    @classmethod
    def build(cls, message: ChatMessage) -> Self:
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            meta=message.meta,
            created_at=message.created_at,
        )


class ThreadMessages(BaseModel):
    thread: ThreadRead
    messages: list[MessageRead]
    # The confirmation waiting for the user, so the page can show the card again after
    # a reload: {"action", "tool_call_id", "summary"}.
    pending_action: dict[str, Any] | None


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MESSAGE_MAX_LENGTH)
    ]


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirm", "cancel"]
