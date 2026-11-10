"""What a run writes: its full configuration and one row per case.

`evals/agent/runs/<run id>/run.json` holds everything needed to say what was measured:
the model as configured and as the provider reported it, the reasoning setting, the
serving details for a self-hosted model, timeouts and concurrency, the git commit, and
hashes of the dataset, the system prompt, and the tool schemas.
`cases.jsonl.gz` holds every case's checks, cards, tool calls, answers, latencies, and
tokens, so two runs can be compared case by case. Both are written byte-stable.
"""

import gzip
import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Final

from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent.evaluation.cases import EVAL_NOW
from app.agent.evaluation.runner import CaseResult, Options
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import build_tools
from app.core.config import Settings
from app.llm.registry import PROVIDERS

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
EVALS_DIR: Final = REPO_ROOT / "evals" / "agent"
CASES_PATH: Final = EVALS_DIR / "cases.jsonl"
RUNS_DIR: Final = EVALS_DIR / "runs"
QUERY_EMBEDDINGS_PATH: Final = EVALS_DIR / "query_embeddings.json.gz"
SERVING_FILE: Final = REPO_ROOT / "serving" / "modal_vllm.py"
PACKAGES: Final = (
    "langchain-core",
    "langchain-openai",
    "langchain-anthropic",
    "langgraph",
    "langgraph-checkpoint-postgres",
    "openai",
    "anthropic",
)


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed arguments
        ["git", *args],  # noqa: S607 - git from PATH
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_state() -> tuple[str, bool]:
    commit = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return commit, dirty


def prompt_hash() -> str:
    tools = [convert_to_openai_tool(tool) for tool in build_tools()]
    blob = json.dumps({"system": SYSTEM_PROMPT, "tools": tools}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def reasoning_setting(settings: Settings) -> str:
    provider, _, _ = settings.assistant_model.partition(":")
    if provider == "vllm":
        thinking = settings.vllm_enable_thinking
        return "thinking=default" if thinking is None else f"thinking={'on' if thinking else 'off'}"
    entry = PROVIDERS.get(provider)
    if entry is not None and entry.reasoning_effort:
        return f"effort={entry.reasoning_effort}"
    return "none"


def serving_details() -> dict[str, str]:
    """The pinned revision, vLLM version, and GPU from the deployment file."""
    text = SERVING_FILE.read_text(encoding="utf-8")
    details: dict[str, str] = {}
    for key in ("MODEL_REVISION", "VLLM_VERSION", "GPU"):
        match = re.search(rf'^{key}: Final = "([^"]+)"', text, re.MULTILINE)
        if match:
            details[key.lower()] = match.group(1)
    return details


def _versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return versions


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def run_id(settings: Settings, started: datetime, repeat: int) -> str:
    model = settings.assistant_model.partition(":")[2].rsplit("/", 1)[-1]
    reasoning = reasoning_setting(settings).replace("=", "-")
    return f"{started:%Y%m%d-%H%M}-{slug(model)}-{reasoning}-r{repeat}"


def run_config(
    settings: Settings,
    options: Options,
    *,
    run: str,
    repeat: int,
    cases_path: Path,
    case_ids: Sequence[str],
    commit: str,
    dirty: bool,
    cold_start_seconds: float | None,
    started: datetime,
    finished: datetime,
    reported_models: Sequence[str],
) -> dict[str, Any]:
    provider, _, model = settings.assistant_model.partition(":")
    config: dict[str, Any] = {
        "run_id": run,
        "repeat": repeat,
        "assistant_model": settings.assistant_model,
        "provider": provider,
        "model": model,
        "reported_models": sorted(set(reported_models)),
        "reasoning": reasoning_setting(settings),
        "request_timeout_seconds": options.request_timeout_seconds,
        "request_retries": 0,
        "run_timeout_seconds": options.run_timeout_seconds,
        "concurrency": options.concurrency,
        "case_attempts": options.attempts,
        "backoff_seconds": list(options.backoff_seconds),
        "git_commit": commit,
        "git_dirty": dirty,
        "dataset": str(cases_path.relative_to(REPO_ROOT))
        if cases_path.is_relative_to(REPO_ROOT)
        else str(cases_path),
        "dataset_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "cases": list(case_ids),
        "prompt_sha256": prompt_hash(),
        "eval_now": EVAL_NOW.isoformat(),
        "packages": _versions(),
        "cold_start_seconds": cold_start_seconds,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    if provider == "vllm":
        config["serving"] = serving_details()
    return config


def summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if r.status != "infra"]
    by_category: dict[str, list[int]] = {}
    for result in scored:
        counts = by_category.setdefault(result.category, [0, 0])
        counts[0] += int(result.passed)
        counts[1] += 1
    return {
        "cases": len(results),
        "scored": len(scored),
        "passed": sum(r.passed for r in scored),
        "excluded_infra": len(results) - len(scored),
        "wrong_cards": sum(r.wrong_cards for r in scored),
        "statuses": dict(sorted(Counter(r.status for r in results).items())),
        "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in sorted(by_category.items())},
    }


def _write_gzip_lines(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(lines.encode("utf-8"))


def write_run(directory: Path, config: dict[str, Any], results: Sequence[CaseResult]) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    record = {**config, "summary": summary(results)}
    (directory / "run.json").write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    _write_gzip_lines(directory / "cases.jsonl.gz", [r.as_dict() for r in results])


def read_cases(directory: Path) -> list[dict[str, Any]]:
    with gzip.open(directory / "cases.jsonl.gz", "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]
