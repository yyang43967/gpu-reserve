"""LLM judges for the free-text criteria of judged cases.

Every judged answer gets two verdicts on each of its criteria: one from the primary
judge, which decides the case, and one from a second judge, used only to measure how
far the judges agree. Neither judge is told which model wrote the answer. Both read the
committed rubric (evals/agent/rubric.md); its version and hash are stored with every
verdict, and verdicts are cached by everything they depend on, so judging a run again
with the same rubric costs nothing.

The second judge exists because the primary judge is also one of the models being
compared: agreement between the two judges, broken down by the model that wrote the
answer, shows whether the primary judge favors its own answers. Hand labels on a sample
are the ground truth for both.
"""

import asyncio
import gzip
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.agent.evaluation.cases import Case

PRIMARY_JUDGE: Final = "anthropic:claude-haiku-5-5"
SECOND_JUDGE: Final = "openai:gpt-5.4-mini-2026-03-17"
JUDGES: Final = (PRIMARY_JUDGE, SECOND_JUDGE)
# A reply that is not the requested JSON is asked for once more.
PARSE_ATTEMPTS: Final = 2
JUDGE_CONCURRENCY: Final = 4
_VERSION: Final = re.compile(r"^Version: (\S+)$", re.MULTILINE)
_JSON: Final = re.compile(r"\{.*\}", re.DOTALL)


class _Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    reason: str


class _Reply(BaseModel):
    verdicts: dict[str, _Verdict]


@dataclass(frozen=True)
class Rubric:
    text: str
    version: str
    sha256: str

    @classmethod
    def load(cls, path: Path) -> "Rubric":
        text = path.read_text(encoding="utf-8")
        match = _VERSION.search(text)
        if match is None:
            raise ValueError(f"{path} has no 'Version:' line")
        return cls(text, match.group(1), hashlib.sha256(text.encode()).hexdigest())


@dataclass
class Judgment:
    case_id: str
    judge: str
    rubric_version: str
    rubric_sha256: str
    # criterion -> {"verdict": "pass" | "fail", "reason": str}; empty when the judge
    # never returned a usable reply (see `error`).
    verdicts: dict[str, dict[str, str]] = field(default_factory=dict)
    error: str | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cached: bool = False

    @property
    def passed(self) -> bool | None:
        if self.error or not self.verdicts:
            return None
        return all(v["verdict"] == "pass" for v in self.verdicts.values())


def judge_input(case: Case, row: dict[str, Any]) -> str:
    """What a judge sees: the user's messages, the retrieved sections, the reference
    facts, the answer, and the criteria. Nothing about which model answered."""
    assert case.judge is not None  # noqa: S101 - only judged cases get here
    sections = [
        f"[{hit.get('ref')}] {hit.get('document')} > {hit.get('heading')}\n{hit.get('content')}"
        for hit in row.get("retrieved", [])
    ]
    answers = row.get("answers") or []
    parts = [
        "User:\n" + "\n".join(turn.say for turn in case.turns),
        "Retrieved sections:\n" + ("\n\n".join(sections) if sections else "(none)"),
        "Reference facts:\n" + ("\n".join(f"- {f}" for f in case.judge.reference) or "(none)"),
        "Assistant's answer:\n" + (answers[-1] if answers else "(no answer)"),
        "Criteria to grade: " + ", ".join(case.judge.criteria),
    ]
    return "\n\n".join(parts)


def cache_key(rubric: Rubric, judge: str, case: Case, row: dict[str, Any]) -> str:
    blob = json.dumps(
        {"rubric": rubric.sha256, "judge": judge, "input": judge_input(case, row)},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def parse_reply(text: str, criteria: Sequence[str]) -> dict[str, dict[str, str]]:
    match = _JSON.search(text)
    if match is None:
        raise ValueError("no JSON object in the reply")
    reply = _Reply.model_validate(json.loads(match.group(0)))
    missing = [c for c in criteria if c not in reply.verdicts]
    if missing:
        raise ValueError(f"no verdict for {missing}")
    return {c: reply.verdicts[c].model_dump() for c in criteria}


def _usage(message: AIMessage, judgment: Judgment) -> None:
    usage = message.usage_metadata
    if usage is None:
        return
    details = usage.get("input_token_details") or {}
    judgment.input_tokens += usage.get("input_tokens", 0)
    judgment.cache_read_tokens += details.get("cache_read", 0) or 0
    judgment.cache_write_tokens += details.get("cache_creation", 0) or 0
    judgment.output_tokens += usage.get("output_tokens", 0)


async def judge_one(
    model: BaseChatModel, judge: str, rubric: Rubric, case: Case, row: dict[str, Any]
) -> Judgment:
    assert case.judge is not None  # noqa: S101 - only judged cases get here
    judgment = Judgment(case.id, judge, rubric.version, rubric.sha256)
    messages: list[Any] = [SystemMessage(rubric.text), HumanMessage(judge_input(case, row))]
    for _ in range(PARSE_ATTEMPTS):
        try:
            reply = await model.ainvoke(messages)
        except Exception as exc:
            judgment.error = f"{type(exc).__name__}"
            return judgment
        assert isinstance(reply, AIMessage)  # noqa: S101 - chat models return AIMessage
        _usage(reply, judgment)
        try:
            judgment.verdicts = parse_reply(str(reply.text), case.judge.criteria)
            judgment.error = None
            return judgment
        except (ValueError, ValidationError) as exc:
            judgment.error = f"unparsable reply: {exc}"[:300]
            messages = [
                *messages,
                reply,
                HumanMessage("Reply with only the JSON object described in the rubric."),
            ]
    return judgment


class JudgeCache:
    """Verdicts by cache key, kept in one JSON-lines file next to the dataset."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.entries[entry["key"]] = entry
        self._new: list[dict[str, Any]] = []

    def get(self, key: str) -> Judgment | None:
        entry = self.entries.get(key)
        if entry is None:
            return None
        return Judgment(**{**entry["judgment"], "cached": True})

    def put(self, key: str, judgment: Judgment) -> None:
        if judgment.error:
            return  # failures are retried next time
        entry = {"key": key, "judgment": {**asdict(judgment), "cached": False}}
        self.entries[key] = entry
        self._new.append(entry)

    def save(self) -> None:
        if not self._new:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            for entry in self._new:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        self._new = []


async def judge_rows(
    cases: dict[str, Case],
    rows: Sequence[dict[str, Any]],
    models: dict[str, BaseChatModel],
    rubric: Rubric,
    cache: JudgeCache,
) -> list[Judgment]:
    """Two verdicts for every judged case that ran to an answer."""
    gate = asyncio.Semaphore(JUDGE_CONCURRENCY)
    todo = [
        (judge, cases[row["id"]], row)
        for row in rows
        if row["id"] in cases and cases[row["id"]].judge is not None and row["status"] == "ok"
        for judge in models
    ]

    async def one(judge: str, case: Case, row: dict[str, Any]) -> Judgment:
        key = cache_key(rubric, judge, case, row)
        hit = cache.get(key)
        if hit is not None:
            return hit
        async with gate:
            judgment = await judge_one(models[judge], judge, rubric, case, row)
        cache.put(key, judgment)
        return judgment

    return list(await asyncio.gather(*(one(*item) for item in todo)))


def write_judgments(directory: Path, judgments: Sequence[Judgment]) -> None:
    lines = "".join(
        json.dumps(asdict(j), sort_keys=True) + "\n"
        for j in sorted(judgments, key=lambda j: (j.case_id, j.judge))
    )
    with (
        (directory / "judgments.jsonl.gz").open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz,
    ):
        gz.write(lines.encode("utf-8"))


def read_judgments(directory: Path) -> list[Judgment]:
    path = directory / "judgments.jsonl.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [Judgment(**json.loads(line)) for line in handle if line.strip()]
