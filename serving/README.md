# Self-hosted model on Modal

vLLM serves `Qwen/Qwen3.6-35B-A3B-FP8` behind an OpenAI-compatible API on one Modal
GPU. The backend reaches it with `ASSISTANT_MODEL=vllm:Qwen/Qwen3.6-35B-A3B-FP8`,
`VLLM_BASE_URL=<endpoint>/v1`, and `VLLM_API_KEY`.

## Configuration

| | |
|---|---|
| GPU | 1 × L40S (48 GB, Ada), $1.95/h |
| Model | `Qwen/Qwen3.6-35B-A3B-FP8` |
| Revision | `c653bba46dfa06e9658dcc0b0dc87fe4685f14a2` (2026-11-07) |
| vLLM | 0.32.0 on `nvidia/cuda:12.8.1-devel-ubuntu22.04`, Python 3.12 |
| Served model name | `Qwen/Qwen3.6-35B-A3B-FP8` (equals the model part of `ASSISTANT_MODEL`) |
| Containers | at most 1, 16 concurrent requests, scaled down after 3 idle minutes |
| Volumes | `gpu-reserve-hf-cache` (weights), `gpu-reserve-vllm-cache` (torch.compile cache) |
| Secret | `gpu-reserve-vllm` holding `VLLM_API_KEY` |

Launch flags:

```
vllm serve Qwen/Qwen3.6-35B-A3B-FP8 --revision <sha>
  --served-model-name Qwen/Qwen3.6-35B-A3B-FP8
  --api-key $VLLM_API_KEY
  --max-model-len 32768
  --max-num-seqs 16
  --language-model-only
  --enable-auto-tool-choice --tool-call-parser qwen3_coder
  --reasoning-parser qwen3
  [--enforce-eager]            # only with FAST_BOOT=1
```

- `--max-num-seqs 16`: the model keeps one block of linear-attention state per running
  sequence. With CUDA graphs on, the default of 256 sequences does not fit: 0.89 GiB of
  graphs left room for 255 blocks, and the engine refused to start. The container takes
  at most 16 requests at once, so the limit costs nothing.
- `--language-model-only` skips the vision encoder; the agent sends text only.

Memory with CUDA graphs: weights take 33.3 GiB and the graphs 0.3 GiB. The KV cache gets
about 6–7 GiB at the default `gpu_memory_utilization` (0.92), which holds about 260,000
tokens, or 7.9 requests at the full 32,768-token length.

## Usage

```bash
pip install modal==1.6.3 && modal setup
modal secret create gpu-reserve-vllm VLLM_API_KEY=<random key>
modal run serving/modal_vllm.py::download_weights   # once, on CPU (about 2.5 minutes)
modal deploy serving/modal_vllm.py                  # FAST_BOOT=1 for --enforce-eager
```

The weights are downloaded by a CPU-only function, so no GPU is billed while 37.5 GB
arrive. The first request after an idle period starts a container. `/health` is open;
everything under `/v1` needs the key.

From `backend/`, with `VLLM_BASE_URL` and `VLLM_API_KEY` set:

```bash
PYTHONPATH=. uv run python ../serving/smoke_tool_call.py   # one tool call through the provider
uv run python ../serving/bench_decode.py                   # decode throughput
```

### Between sessions

Deploy only for a session of evaluation or testing, and stop the app when it ends:

```bash
modal deploy serving/modal_vllm.py      # start of a session
modal app stop -y gpu-reserve-vllm      # end of a session
```

A deployed app starts a GPU container for any request that reaches the endpoint,
including a retry or a request Modal still holds in its queue. During bring-up, stopping
a container was not enough: Modal started a new one twice to serve a request that was
still queued. A stopped app has no endpoint, so nothing can wake a GPU. Check with
`modal container list` that nothing is left running.

## Measurements

Cold start is the time from the first request with no container running to the first
`200` on `/health`, measured from the client. The weights were already in the volume.

| Boot | Compile cache | Cold start | Weights load | Engine init (of which compile) |
|---|---|---|---|---|
| `--enforce-eager` | not used | 177 s | 42 s | 66 s |
| CUDA graphs + torch.compile | empty | 289 s | 29 s | 203 s (35 s) |
| CUDA graphs + torch.compile | warm | **184 s** | 20 s | 131 s (15 s) |

A warm compile cache saves about 20 s of compilation. Most of the remaining engine time
is profiling, kernel warm-up, and graph capture, which happen on every boot.

Decode speed, 1,024 generated tokens per request:

| | Eager | CUDA graphs |
|---|---|---|
| Single stream, thinking on | ~12 tok/s | 138 tok/s |
| Single stream, thinking off | — | 137 tok/s |
| 8 concurrent requests | — | 478 tok/s total, 65 tok/s per stream |

Time to first token was 0.4 s for a short prompt.

One agent step (system prompt, user request, `find_slots` tool), 4 requests each:

| Thinking | Output tokens | Latency | Valid tool calls |
|---|---|---|---|
| on (model default) | 474–1,806 | 3.9–13.6 s, median 8.5 s | 4 / 4 |
| off | 135 | 1.3–1.4 s | 4 / 4 |

Tool calling: the `qwen3_coder` parser returned the `find_slots` call with
`finish_reason=tool_calls` every time. Its arguments validated against the tool's schema,
with ISO 8601 times including the offset and `gpu_model` mapped to the `H100_80GB` value.

## Cost

L40S time for the whole bring-up (weights download excluded, since it ran on CPU) was
about 25 minutes, $0.80. A cold start costs about $0.10 and the idle window before scale
down another $0.10.
