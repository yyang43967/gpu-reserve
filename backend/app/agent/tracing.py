"""Langfuse tracing for assistant runs, through its LangChain callback handler.

Off unless both keys are set. Traces carry the thread id as the session and the user's
UUID as the user; never an email or a name.
"""

import logging
import uuid
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from app.core.config import Settings

logger = logging.getLogger(__name__)


class Tracing:
    def __init__(self, settings: Settings) -> None:
        from langfuse import Langfuse

        assert settings.langfuse_public_key is not None  # noqa: S101 - checked by caller
        assert settings.langfuse_secret_key is not None  # noqa: S101
        self._public_key = settings.langfuse_public_key.get_secret_value()
        self._client = Langfuse(
            public_key=self._public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host,
            environment=settings.environment.value,
        )

    def handler(self) -> BaseCallbackHandler:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(public_key=self._public_key)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception:
            logger.warning("Langfuse shutdown failed", exc_info=True)


def build_tracing(settings: Settings) -> Tracing | None:
    if not settings.tracing_enabled:
        return None
    return Tracing(settings)


def trace_metadata(*, thread_id: uuid.UUID, user_id: uuid.UUID, kind: str) -> dict[str, Any]:
    """Run metadata Langfuse reads as trace attributes. Ids only."""
    return {
        "langfuse_session_id": str(thread_id),
        "langfuse_user_id": str(user_id),
        "langfuse_tags": ["assistant", kind],
    }
