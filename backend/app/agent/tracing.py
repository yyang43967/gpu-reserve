"""Langfuse tracing for assistant runs, through its LangChain callback handler.

Off unless both keys are set. Traces carry the thread id as the session and the user's
UUID as the user; never an email or a name.
"""

import asyncio
import logging
import uuid
from typing import Any, Final

from langchain_core.callbacks import BaseCallbackHandler

from app.core.config import Settings

logger = logging.getLogger(__name__)

# The one-time credentials check at startup gives up after this long.
AUTH_CHECK_TIMEOUT_SECONDS: Final = 10.0


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

    async def verify(self) -> bool:
        """Check the keys once, in the background. Spans are exported asynchronously,
        so wrong credentials (such as keys from one region with the host of another)
        would otherwise only show up as export errors deep in the logs."""
        try:
            async with asyncio.timeout(AUTH_CHECK_TIMEOUT_SECONDS):
                return await asyncio.to_thread(self._client.auth_check)
        except Exception:
            logger.warning(
                "Langfuse did not accept the tracing credentials; traces will not be "
                "exported. Check that LANGFUSE_HOST is the region the keys belong to."
            )
            return False

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
