"""Records every model call of a case: latency, tokens, the model that answered, and
how a failed call failed.

Failures are sorted into three kinds, because they are scored differently:
- `timeout`: a model request hit the request timeout, or the case's run hit the
  assistant's run cap. A slow model is the model's problem, so this counts as a failed
  case. (`step_limit`, when the model keeps calling tools past the graph's step limit,
  is decided by the driver and also counts.)
- `infra`: rate limits, server errors, and lost connections. These say nothing about
  the model; the runner retries the case and leaves it out if they persist.
- `error`: anything else, such as a request the provider rejected. Counted as failed.
"""

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import anthropic
import openai
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.agent.reasoning import message_model


class Failure(StrEnum):
    TIMEOUT = "timeout"
    # The run stopped at the graph's step limit: the model kept calling tools.
    STEP_LIMIT = "step_limit"
    INFRA = "infra"
    ERROR = "error"


_RETRYABLE_STATUS = 429
_CACHE_WRITE_LIFETIMES = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


def cache_write_tokens(details: Mapping[str, Any]) -> int:
    """Tokens written to the prompt cache. langchain-anthropic leaves `cache_creation`
    at 0 and reports the writes by lifetime instead (`ephemeral_5m_input_tokens`,
    `ephemeral_1h_input_tokens`); their sum is the provider's
    `cache_creation_input_tokens`."""
    by_lifetime = sum(int(details.get(k) or 0) for k in _CACHE_WRITE_LIFETIMES)
    return max(int(details.get("cache_creation") or 0), by_lifetime)


def classify(exc: BaseException) -> Failure:
    # Timeouts first: both SDKs make their timeout a subclass of the connection error.
    if isinstance(exc, (openai.APITimeoutError, anthropic.APITimeoutError, TimeoutError)):
        return Failure.TIMEOUT
    if isinstance(exc, (openai.APIConnectionError, anthropic.APIConnectionError)):
        return Failure.INFRA
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == _RETRYABLE_STATUS or status >= 500):
        return Failure.INFRA
    return Failure.ERROR


@dataclass
class ModelCall:
    seconds: float
    model: str | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    stop_reason: str | None = None
    failure: Failure | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in self.__dict__.items()}


@dataclass
class Recorder(AsyncCallbackHandler):
    calls: list[ModelCall] = field(default_factory=list)
    _started: dict[uuid.UUID, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__()

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        self._started[run_id] = time.perf_counter()

    def _elapsed(self, run_id: uuid.UUID) -> float:
        started = self._started.pop(run_id, None)
        return 0.0 if started is None else time.perf_counter() - started

    async def on_llm_end(self, response: LLMResult, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        call = ModelCall(seconds=self._elapsed(run_id))
        generation = response.generations[0][0] if response.generations else None
        if isinstance(generation, ChatGeneration) and isinstance(generation.message, AIMessage):
            message = generation.message
            call.model = message_model(message)
            meta = message.response_metadata
            call.stop_reason = meta.get("stop_reason") or meta.get("finish_reason")
            usage = message.usage_metadata
            if usage is not None:
                details_in = usage.get("input_token_details") or {}
                details_out = usage.get("output_token_details") or {}
                call.input_tokens = usage.get("input_tokens", 0)
                call.output_tokens = usage.get("output_tokens", 0)
                call.cache_read_tokens = details_in.get("cache_read", 0) or 0
                call.cache_write_tokens = cache_write_tokens(details_in)
                call.reasoning_tokens = details_out.get("reasoning", 0) or 0
        self.calls.append(call)

    async def on_llm_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        self.calls.append(
            ModelCall(
                seconds=self._elapsed(run_id),
                failure=classify(error),
                error=type(error).__name__,
            )
        )

    def worst_failure(self) -> Failure | None:
        """The failure that decides how a broken case is scored: a timeout outweighs an
        infrastructure error, which outweighs anything else."""
        kinds = {call.failure for call in self.calls if call.failure is not None}
        for kind in (Failure.TIMEOUT, Failure.INFRA, Failure.ERROR):
            if kind in kinds:
                return kind
        return None
