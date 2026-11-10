"""Chat models by name: "<provider>:<model>".

Each provider declares which API it speaks and how. The agent only calls
`build_chat_model` and reads `message.text`, so a provider on chat completions (a vLLM
server), one on the Responses API (OpenAI), and one on the Messages API (Anthropic) are
interchangeable to it.

`scripted` is a deterministic stand-in for end-to-end tests and is refused outside
ENVIRONMENT=test.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import Environment, Settings
from app.core.exceptions import AssistantUnavailable


class Api(StrEnum):
    # /v1/responses. Needed for OpenAI reasoning models that call tools.
    RESPONSES = "responses"
    # /v1/chat/completions. What OpenAI-compatible servers such as vLLM implement.
    CHAT_COMPLETIONS = "chat_completions"
    # Anthropic's /v1/messages, through its own SDK.
    MESSAGES = "messages"


@dataclass(frozen=True)
class Provider:
    api: Api
    # None for models without a reasoning setting.
    reasoning_effort: str | None = None


PROVIDERS: Final[dict[str, Provider]] = {
    # Current OpenAI models only combine a reasoning effort with function tools on the
    # Responses API. Low effort keeps a tool-calling turn fast: the work is choosing
    # tools and arguments, not long derivations.
    "openai": Provider(api=Api.RESPONSES, reasoning_effort="low"),
    # Same reasoning level as OpenAI, so the comparison is between models, not settings.
    "anthropic": Provider(api=Api.MESSAGES, reasoning_effort="low"),
    # Reasoning on a vLLM server is set where the model is served (chat template).
    "vllm": Provider(api=Api.CHAT_COMPLETIONS),
}

# Output cap per Anthropic call (the API requires one). Thinking counts against it; at
# low effort an agent step needs a small fraction of this.
ANTHROPIC_MAX_TOKENS: Final = 8192


def openai_compatible(
    provider: Provider,
    model: str,
    *,
    api_key: SecretStr,
    settings: Settings,
    base_url: str | None = None,
) -> ChatOpenAI:
    """A model behind an OpenAI-compatible API.

    On the Responses API nothing is kept on the provider's side: `store=False`, every
    request carries the full (trimmed) history from our checkpoint, and no
    `previous_response_id` is sent. Reasoning is returned encrypted so it can be sent
    back on the next call of the same run without the provider storing it.
    """
    common = {
        "model_name": model,
        "openai_api_key": api_key,
        "openai_api_base": base_url,
        "request_timeout": settings.assistant_request_timeout_seconds,
        "max_retries": settings.assistant_request_retries,
        "stream_usage": True,
    }
    reasoning = {"effort": provider.reasoning_effort} if provider.reasoning_effort else None
    if provider.api is Api.RESPONSES:
        return ChatOpenAI(
            **common,  # type: ignore[arg-type]
            use_responses_api=True,
            store=False,
            use_previous_response_id=False,
            include=["reasoning.encrypted_content"] if reasoning else None,
            reasoning=reasoning,
        )
    return ChatOpenAI(
        **common,  # type: ignore[arg-type]
        use_responses_api=False,
        reasoning_effort=provider.reasoning_effort,
    )


def anthropic_native(
    provider: Provider, model: str, *, api_key: SecretStr, settings: Settings
) -> ChatAnthropic:
    """A Claude model through Anthropic's SDK.

    Thinking is adaptive, with the effort set explicitly (the API's default differs by
    model). The request-level `cache_control` caches the longest prefix that repeats
    (tools, system prompt, and the history so far), so each step of a tool loop reads
    the previous step's prefix from the cache.
    """
    return ChatAnthropic(  # type: ignore[call-arg]
        model=model,
        api_key=api_key,
        max_tokens=ANTHROPIC_MAX_TOKENS,
        thinking={"type": "adaptive"},
        effort=provider.reasoning_effort,
        default_request_timeout=settings.assistant_request_timeout_seconds,
        max_retries=settings.assistant_request_retries,
        stream_usage=True,
        model_kwargs={"cache_control": {"type": "ephemeral"}},
    )


def build_chat_model(settings: Settings) -> BaseChatModel:
    name, _, model = settings.assistant_model.partition(":")
    if not model:
        raise AssistantUnavailable(
            f"ASSISTANT_MODEL must look like 'provider:model', not {settings.assistant_model!r}."
        )
    if name == "scripted":
        if settings.environment is not Environment.TEST:
            raise AssistantUnavailable("The scripted model is only available in tests.")
        from app.llm.scripted import ScriptedChatModel, e2e_responder

        return ScriptedChatModel(responder=e2e_responder)
    provider = PROVIDERS.get(name)
    if provider is None:
        raise AssistantUnavailable(f"Unknown model provider {name!r}.")
    if name == "anthropic":
        if settings.anthropic_api_key is None:
            raise AssistantUnavailable("ANTHROPIC_API_KEY is not set.")
        return anthropic_native(
            provider, model, api_key=settings.anthropic_api_key, settings=settings
        )
    if name == "vllm":
        if settings.vllm_base_url is None or settings.vllm_api_key is None:
            raise AssistantUnavailable("VLLM_BASE_URL and VLLM_API_KEY must both be set.")
        # The name sent is the served model name, so responses carry the same name and
        # the agent recognizes its own messages.
        return openai_compatible(
            provider,
            model,
            api_key=settings.vllm_api_key,
            settings=settings,
            base_url=settings.vllm_base_url,
        )
    if settings.openai_api_key is None:
        raise AssistantUnavailable("OPENAI_API_KEY is not set.")
    return openai_compatible(provider, model, api_key=settings.openai_api_key, settings=settings)
