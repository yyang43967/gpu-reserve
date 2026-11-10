"""vLLM serving Qwen3.6-35B-A3B (FP8) on one Modal L40S.

Deploy:   modal deploy serving/modal_vllm.py
Weights:  modal run serving/modal_vllm.py::download_weights   (CPU only, run once)
Fast boot (no torch.compile, no CUDA graphs): FAST_BOOT=1 modal deploy serving/modal_vllm.py

The server speaks the OpenAI-compatible API at <url>/v1 and requires the key stored in
the Modal Secret `gpu-reserve-vllm` (VLLM_API_KEY). The backend uses it with
ASSISTANT_MODEL=vllm:Qwen/Qwen3.6-35B-A3B-FP8.
"""

import os
import subprocess
from typing import Final

import modal

MODEL_NAME: Final = "Qwen/Qwen3.6-35B-A3B-FP8"
# Pinned so a later upload (weights or chat template) cannot change results between runs.
MODEL_REVISION: Final = "c653bba46dfa06e9658dcc0b0dc87fe4685f14a2"
# Must equal the model part of ASSISTANT_MODEL, so responses carry the name the agent
# uses to recognize its own messages.
SERVED_MODEL_NAME: Final = MODEL_NAME
VLLM_VERSION: Final = "0.32.0"

GPU: Final = "L40S"
PORT: Final = 8000
MAX_MODEL_LEN: Final = 32768
MINUTES: Final = 60
# Short idle window: an eval run sends requests back to back, and an idle L40S costs
# about $0.03 a minute.
SCALEDOWN_WINDOW: Final = 3 * MINUTES
# Weight loading plus torch.compile and CUDA graph capture on a cold compile cache.
STARTUP_TIMEOUT: Final = 20 * MINUTES
# Concurrent requests per container; the eval runner sends a handful at a time.
MAX_CONCURRENT_INPUTS: Final = 16

# The name of a Modal Secret, not a secret value.
SECRET_NAME: Final = "gpu-reserve-vllm"  # noqa: S105
HF_CACHE: Final = "/root/.cache/huggingface"
VLLM_CACHE: Final = "/root/.cache/vllm"

# Read on the machine that deploys, then baked into the image environment so the
# container sees the same value.
if modal.is_local():
    FAST_BOOT = os.environ.get("FAST_BOOT", "0") == "1"
else:
    FAST_BOOT = os.environ["FAST_BOOT"] == "1"

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(f"vllm=={VLLM_VERSION}", "huggingface_hub[hf_xet]")
    .env(
        {
            "HF_HOME": HF_CACHE,
            "HF_XET_HIGH_PERFORMANCE": "1",
            "FAST_BOOT": "1" if FAST_BOOT else "0",
        }
    )
)

hf_cache = modal.Volume.from_name("gpu-reserve-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("gpu-reserve-vllm-cache", create_if_missing=True)

app = modal.App("gpu-reserve-vllm")


@app.function(image=image, volumes={HF_CACHE: hf_cache}, timeout=60 * MINUTES)
def download_weights() -> None:
    """Fill the weights volume without holding a GPU during the download."""
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_NAME, revision=MODEL_REVISION)
    hf_cache.commit()


def serve_command(api_key: str) -> list[str]:
    command = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--revision",
        MODEL_REVISION,
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        # Inside the container; Modal's proxy is the only way in.
        "0.0.0.0",  # noqa: S104
        "--port",
        str(PORT),
        "--api-key",
        api_key,
        "--max-model-len",
        str(MAX_MODEL_LEN),
        # One linear-attention state block per running sequence. The default (256) does
        # not fit next to CUDA graphs on a 48 GB card, and the container never takes
        # more than MAX_CONCURRENT_INPUTS requests at once.
        "--max-num-seqs",
        str(MAX_CONCURRENT_INPUTS),
        # The agent only sends text; skipping the vision encoder frees memory for KV.
        "--language-model-only",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        "qwen3_coder",
        "--reasoning-parser",
        "qwen3",
        "--uvicorn-log-level",
        "warning",
    ]
    if FAST_BOOT:
        # Skips torch.compile and CUDA graph capture: faster to start, slower per token.
        command.append("--enforce-eager")
    return command


@app.function(
    image=image,
    gpu=GPU,
    max_containers=1,
    scaledown_window=SCALEDOWN_WINDOW,
    timeout=10 * MINUTES,
    volumes={HF_CACHE: hf_cache, VLLM_CACHE: vllm_cache},
    secrets=[modal.Secret.from_name(SECRET_NAME)],
)
@modal.concurrent(max_inputs=MAX_CONCURRENT_INPUTS)
@modal.web_server(port=PORT, startup_timeout=STARTUP_TIMEOUT)
def serve() -> None:
    # A fixed argument list, no shell.
    subprocess.Popen(serve_command(os.environ["VLLM_API_KEY"]))  # noqa: S603
