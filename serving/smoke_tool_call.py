"""One tool-call request through the backend's vllm provider.

Run from backend/ with VLLM_BASE_URL and VLLM_API_KEY set (backend/.env is read):

    uv run python ../serving/smoke_tool_call.py

It binds the real `find_slots` argument schema, asks a question that needs it, and
checks that the server's tool-call parser returned one call whose arguments validate.
Exits non-zero otherwise.
"""

import os
import sys
import time

# Settings needs a JWT secret; nothing here signs tokens.
os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ["ASSISTANT_MODEL"] = "vllm:Qwen/Qwen3.6-35B-A3B-FP8"

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.tools import FindSlotsArgs
from app.core.config import Settings
from app.llm.registry import build_chat_model

FIND_SLOTS = {
    "type": "function",
    "function": {
        "name": "find_slots",
        "description": "Find free GPU slots that match the request.",
        "parameters": FindSlotsArgs.model_json_schema(),
    },
}


def main() -> int:
    model = build_chat_model(Settings())  # type: ignore[call-arg]
    bound = model.bind_tools([FIND_SLOTS])
    messages = [
        SystemMessage(
            "You help students reserve lab GPUs. The current time is "
            "2026-11-10T09:00:00-08:00. Use tools to answer."
        ),
        HumanMessage(
            "Find me 2 H100 GPUs for 3 hours, any time tomorrow between 9am and 6pm Pacific."
        ),
    ]
    started = time.perf_counter()
    response = bound.invoke(messages)
    elapsed = time.perf_counter() - started

    print(f"model_name: {response.response_metadata.get('model_name')}")
    print(f"finish_reason: {response.response_metadata.get('finish_reason')}")
    print(f"latency_s: {elapsed:.2f}")
    print(f"usage: {response.usage_metadata}")
    reasoning = response.additional_kwargs.get("reasoning_content") or ""
    print(f"reasoning_chars: {len(reasoning)}")
    print(f"text: {response.text!r}")
    print(f"invalid_tool_calls: {response.invalid_tool_calls}")
    print(f"tool_calls: {response.tool_calls}")

    if len(response.tool_calls) != 1 or response.tool_calls[0]["name"] != "find_slots":
        print("FAIL: expected exactly one find_slots call")
        return 1
    args = FindSlotsArgs.model_validate(response.tool_calls[0]["args"])
    print(f"validated: {args!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
