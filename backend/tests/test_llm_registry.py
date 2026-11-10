"""Model names and the provider registry."""

from typing import Any

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.agent.graph import build_graph
from app.core.config import Settings
from app.core.exceptions import AssistantUnavailable
from app.llm import registry
from app.llm.names import PROVIDER_PREFIXES, chat_model_name, normalize_model_name, same_model
from app.llm.registry import build_chat_model

OPENAI_MODEL = "gpt-5.4-mini-2026-03-17"
ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_SNAPSHOT = "claude-haiku-5-5-20261001"
VLLM_MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"


# --- names ---


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("gpt-5.4-mini-2026-03-17", "gpt-5.4-mini"),
        ("openai:gpt-5.4-mini-2026-03-17", "gpt-5.4-mini-2026-03-17"),
        (ANTHROPIC_SNAPSHOT, ANTHROPIC_MODEL),
        ("anthropic:claude-haiku-5-5", ANTHROPIC_SNAPSHOT),
        (f"vllm:{VLLM_MODEL}", VLLM_MODEL),
    ],
)
def test_same_model_despite_prefix_or_date(a: str, b: str) -> None:
    assert same_model(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("gpt-5.4", "gpt-5.4-mini"),
        ("gpt-5.4-2026-03-17", "gpt-5.4-mini-2026-03-17"),
        ("claude-haiku-5", "claude-haiku-5-5"),
        ("claude-haiku-5", ANTHROPIC_SNAPSHOT),
        ("claude-sonnet-5-5", ANTHROPIC_MODEL),
        ("Qwen/Qwen3.6-35B-A3B", VLLM_MODEL),
    ],
)
def test_different_models_never_match(a: str, b: str) -> None:
    assert not same_model(a, b)


def test_only_a_trailing_date_and_a_known_prefix_are_removed() -> None:
    assert normalize_model_name("model-2026-03-17-preview") == "model-2026-03-17-preview"
    assert normalize_model_name("other:model") == "other:model"
    assert set(registry.PROVIDERS) | {"scripted"} <= PROVIDER_PREFIXES


# --- registry ---


def _settings(model: str, **values: Any) -> Settings:
    return Settings(assistant_model=model, **values)


def test_each_provider_needs_its_own_credentials() -> None:
    openai_only = {"openai_api_key": "sk-test"}
    with pytest.raises(AssistantUnavailable, match="ANTHROPIC_API_KEY"):
        build_chat_model(_settings(f"anthropic:{ANTHROPIC_MODEL}", **openai_only))
    with pytest.raises(AssistantUnavailable, match="VLLM_BASE_URL"):
        build_chat_model(_settings(f"vllm:{VLLM_MODEL}", **openai_only))
    with pytest.raises(AssistantUnavailable, match="VLLM_BASE_URL"):
        build_chat_model(_settings(f"vllm:{VLLM_MODEL}", vllm_base_url="http://vllm.test/v1"))
    with pytest.raises(AssistantUnavailable, match="OPENAI_API_KEY"):
        build_chat_model(_settings(f"openai:{OPENAI_MODEL}", anthropic_api_key="sk-ant-test"))


def test_anthropic_requests_low_effort_adaptive_thinking_and_caching() -> None:
    model = build_chat_model(
        _settings(f"anthropic:{ANTHROPIC_MODEL}", anthropic_api_key="sk-ant-test")
    )
    assert isinstance(model, ChatAnthropic)
    assert chat_model_name(model) == ANTHROPIC_MODEL

    payload = model._get_request_payload([SystemMessage("rules"), HumanMessage("hi")])

    assert payload["model"] == ANTHROPIC_MODEL
    assert payload["thinking"] == {"type": "adaptive"}
    assert payload["output_config"] == {"effort": "low"}
    assert payload["cache_control"] == {"type": "ephemeral"}
    assert payload["max_tokens"] == registry.ANTHROPIC_MAX_TOKENS


def test_vllm_sends_the_served_model_name_to_its_own_server() -> None:
    model = build_chat_model(
        _settings(
            f"vllm:{VLLM_MODEL}", vllm_base_url="http://vllm.test/v1", vllm_api_key="vllm-test"
        )
    )
    assert isinstance(model, ChatOpenAI)
    assert not model.use_responses_api
    assert model.openai_api_base == "http://vllm.test/v1"
    # The client actually used, not just the field: settings are applied at construction.
    assert str(model.root_async_client.base_url).rstrip("/") == "http://vllm.test/v1"
    assert chat_model_name(model) == VLLM_MODEL

    payload = model._get_request_payload([HumanMessage("hi")])

    assert payload["model"] == VLLM_MODEL
    assert "messages" in payload and "reasoning_effort" not in payload
    assert {"agent", "tools"} <= set(build_graph(model, None).nodes)
