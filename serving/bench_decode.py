"""Decode throughput of the vLLM endpoint.

Run from backend/ (reads VLLM_BASE_URL and VLLM_API_KEY from the environment):

    uv run python ../serving/bench_decode.py

Measures single-stream decode speed with thinking on and off, and aggregate speed at a
concurrency of 8. Decode speed is generated tokens over the time after the first token,
so prompt processing and network setup are left out.
"""

import asyncio
import os
import statistics
import time

from openai import AsyncOpenAI

MODEL = "Qwen/Qwen3.6-35B-A3B-FP8"
MAX_TOKENS = 1024
PROMPT = (
    "Explain, step by step, how a lab could schedule 8 GPUs across 5 research groups "
    "fairly over a week. Be thorough."
)


async def one(client: AsyncOpenAI, thinking: bool) -> tuple[int, float, float]:
    """Returns (completion tokens, time to first token, decode seconds)."""
    started = time.perf_counter()
    first: float | None = None
    tokens = 0
    stream = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=MAX_TOKENS,
        # A fixed length, so every request decodes the same number of tokens.
        extra_body={
            "ignore_eos": True,
            "chat_template_kwargs": {"enable_thinking": thinking},
        },
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in stream:
        if first is None and chunk.choices:
            delta = chunk.choices[0].delta
            if (
                delta.content
                or getattr(delta, "reasoning_content", None)
                or getattr(delta, "reasoning", None)
            ):
                first = time.perf_counter()
        if chunk.usage:
            tokens = chunk.usage.completion_tokens
    ended = time.perf_counter()
    if first is None:
        raise RuntimeError("The stream returned no tokens.")
    return tokens, first - started, ended - first


async def main() -> None:
    client = AsyncOpenAI(
        base_url=os.environ["VLLM_BASE_URL"], api_key=os.environ["VLLM_API_KEY"], timeout=600
    )
    await one(client, thinking=False)  # warm-up

    for thinking in (True, False):
        rates = []
        for _ in range(3):
            tokens, ttft, decode = await one(client, thinking)
            rates.append((tokens - 1) / decode)
        print(
            f"single thinking={'on' if thinking else 'off'}: "
            f"{statistics.median(rates):.1f} tok/s (runs {[round(r, 1) for r in rates]}, "
            f"ttft {ttft:.2f}s)"
        )

    concurrency = 8
    started = time.perf_counter()
    results = await asyncio.gather(*(one(client, True) for _ in range(concurrency)))
    wall = time.perf_counter() - started
    total = sum(r[0] for r in results)
    per_stream = statistics.median((r[0] - 1) / r[2] for r in results)
    print(
        f"concurrency {concurrency}: {total / wall:.1f} tok/s aggregate, "
        f"{per_stream:.1f} tok/s per stream, {total} tokens in {wall:.1f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
