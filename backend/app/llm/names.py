"""Comparing model names as the registry and the providers write them.

The registry says `anthropic:claude-haiku-5-5`; the API answers with a dated snapshot
such as `claude-haiku-5-5-20261001`. Both name the same model. Matching is exact after
removing a known provider prefix and a trailing date, and nothing else: a prefix match
would take `gpt-5.4` for `gpt-5.4-mini`, and send one model's reasoning to another.
"""

import re
from typing import Final

from langchain_core.language_models import BaseChatModel

# Prefixes the registry uses. Model ids such as `Qwen/Qwen3.6-35B-A3B-FP8` contain no
# colon, but an unknown prefix is left alone rather than guessed at.
PROVIDER_PREFIXES: Final = frozenset({"openai", "anthropic", "vllm", "scripted"})
# `-2026-03-17` (OpenAI) or `-20261001` (Anthropic), only at the very end.
_DATE_SUFFIX: Final = re.compile(r"-\d{4}-?\d{2}-?\d{2}$")


def normalize_model_name(name: str) -> str:
    provider, sep, rest = name.partition(":")
    if sep and provider in PROVIDER_PREFIXES:
        name = rest
    return _DATE_SUFFIX.sub("", name)


def same_model(a: str, b: str) -> bool:
    return normalize_model_name(a) == normalize_model_name(b)


def chat_model_name(model: BaseChatModel) -> str | None:
    """The model a chat model sends requests to, as configured."""
    for attribute in ("model_name", "model"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None
