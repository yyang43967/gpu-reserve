"""Chat models by name: "<provider>:<model>".

Only `openai` is registered for now. `scripted` is a deterministic stand-in for
end-to-end tests and is refused outside ENVIRONMENT=test.
"""

from typing import Final

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.core.config import Environment, Settings
from app.core.exceptions import AssistantUnavailable

# Reasoning effort for the assistant's model. Low keeps a tool-calling turn fast; the
# work is choosing tools and arguments, not long derivations.
REASONING_EFFORT: Final = "low"


def build_chat_model(settings: Settings) -> BaseChatModel:
    provider, _, name = settings.assistant_model.partition(":")
    if not name:
        raise AssistantUnavailable(
            f"ASSISTANT_MODEL must look like 'provider:model', not {settings.assistant_model!r}."
        )
    if provider == "openai":
        if settings.openai_api_key is None:
            raise AssistantUnavailable("OPENAI_API_KEY is not set.")
        return ChatOpenAI(
            model_name=name,
            openai_api_key=settings.openai_api_key,
            request_timeout=settings.assistant_request_timeout_seconds,
            max_retries=settings.assistant_request_retries,
            reasoning_effort=REASONING_EFFORT,
            stream_usage=True,
        )
    if provider == "scripted":
        if settings.environment is not Environment.TEST:
            raise AssistantUnavailable("The scripted model is only available in tests.")
        from app.llm.scripted import ScriptedChatModel, e2e_responder

        return ScriptedChatModel(responder=e2e_responder)
    raise AssistantUnavailable(f"Unknown model provider {provider!r}.")
