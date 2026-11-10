"""Runs every case of the dataset against one model configuration.

Each case gets its own copy of the template database, its own assistant built the way
the API builds it (pointed at that copy, with the clock fixed at EVAL_NOW), and a
recorder for its model calls. A case whose model calls fail for infrastructure reasons
(rate limits, server errors, lost connections) is tried again from a fresh copy after a
pause; if it keeps failing it is marked `infra` and left out of the scores. Timeouts are
not retried: they count against the model.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.evaluation.cases import EVAL_NOW, Case
from app.agent.evaluation.database import (
    Databases,
    action_outcomes,
    apply_setup,
    case_engine,
    reservation_snapshot,
    server_ids,
    user_by_email,
)
from app.agent.evaluation.driver import InfraFailure, Transcript, play
from app.agent.evaluation.recorder import Failure, ModelCall, Recorder
from app.agent.evaluation.scoring import Names, Observed, Score, db_diff, score_case
from app.agent.setup import assistant_lifespan
from app.core.config import Settings
from app.db.session import create_session_factory
from app.rag.embeddings import Embedder
from app.services import assistant_threads as threads

logger = logging.getLogger(__name__)

# Pauses before the second and third attempt of a case hit by infrastructure errors.
DEFAULT_BACKOFF_SECONDS: Final = (15.0, 60.0)
# Long enough for a cold start of the self-hosted model (about three minutes measured).
WARM_UP_TIMEOUT_SECONDS: Final = 900.0


@dataclass(frozen=True)
class Options:
    request_timeout_seconds: float = 90.0
    run_timeout_seconds: float = 600.0
    concurrency: int = 4
    backoff_seconds: Sequence[float] = DEFAULT_BACKOFF_SECONDS

    @property
    def attempts(self) -> int:
        return len(self.backoff_seconds) + 1


def eval_settings(settings: Settings, options: Options) -> Settings:
    """The backend's settings with the evaluation's timeouts. Model calls are not
    retried inside a run: a retried timeout would hide a slow model, and infrastructure
    errors are retried by re-running the whole case instead. Tracing is off."""
    return settings.model_copy(
        update={
            "assistant_request_timeout_seconds": options.request_timeout_seconds,
            "assistant_request_retries": 0,
            "assistant_run_timeout_seconds": options.run_timeout_seconds,
            "langfuse_public_key": None,
            "langfuse_secret_key": None,
        }
    )


@dataclass
class CaseResult:
    id: str
    category: str
    attempts: int
    # ok: the run finished; timeout or error: the model failed; infra: left out.
    status: str
    passed: bool
    path: int | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    wrong_cards: int = 0
    card_mismatches: list[list[str] | None] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    db_created: list[dict[str, Any]] = field(default_factory=list)
    db_changed: dict[str, dict[str, Any]] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result(
    case: Case,
    attempts: int,
    transcript: Transcript,
    score: Score,
    seen: Observed,
    calls: list[ModelCall],
    seconds: float,
) -> CaseResult:
    created, changed = db_diff(seen.before, seen.after)
    status = transcript.failure.value if transcript.failure is not None else "ok"
    return CaseResult(
        id=case.id,
        category=case.category.value,
        attempts=attempts,
        status=status,
        passed=score.passed,
        path=score.path,
        checks=[asdict(check) for check in score.checks],
        wrong_cards=score.wrong_cards,
        card_mismatches=score.card_mismatches,
        cards=[asdict(card) for card in transcript.cards],
        tool_calls=[
            {"name": c.name, "args": c.args, "ok": c.error_type is None, "error": c.error_type}
            for c in transcript.tool_calls
        ],
        answers=transcript.answers,
        citations=transcript.citations,
        errors=transcript.errors,
        segments=[asdict(segment) for segment in transcript.segments],
        model_calls=[call.as_dict() for call in calls],
        db_created=created,
        db_changed=changed,
        actions=seen.actions,
        seconds=seconds,
    )


@dataclass
class CaseRunner:
    databases: Databases
    settings: Settings
    model: BaseChatModel
    embedder: Embedder
    options: Options
    sleep: Callable[[float], Any] = asyncio.sleep

    def __post_init__(self) -> None:
        self._next_index = 0

    def _index(self) -> int:
        self._next_index += 1
        return self._next_index

    async def run(self, case: Case) -> CaseResult:
        for attempt in range(1, self.options.attempts + 1):
            try:
                return await self._attempt(case, attempt)
            except InfraFailure as exc:
                logger.warning("Case %s, attempt %d: %s", case.id, attempt, exc)
                if attempt < self.options.attempts:
                    await self.sleep(self.options.backoff_seconds[attempt - 1])
        return CaseResult(
            id=case.id,
            category=case.category.value,
            attempts=self.options.attempts,
            status=Failure.INFRA.value,
            passed=False,
        )

    async def _attempt(self, case: Case, attempt: int) -> CaseResult:
        started = time.perf_counter()
        async with self.databases.case_database(self._index()) as url:
            engine = case_engine(url)
            try:
                return await self._play(case, attempt, url, engine, started)
            finally:
                await engine.dispose()

    async def _play(
        self, case: Case, attempt: int, url: str, engine: AsyncEngine, started: float
    ) -> CaseResult:
        settings = self.settings.model_copy(update={"database_url": PostgresDsn(url)})
        sessions = create_session_factory(engine)
        quota_zone = settings.quota_zone
        await apply_setup(sessions, case.setup, now=EVAL_NOW, quota_zone=quota_zone)
        before = await reservation_snapshot(sessions)
        async with sessions() as session, session.begin():
            user = await user_by_email(session, case.user)
            thread = await threads.create_thread(session, user, None)
            servers = await server_ids(session)
        recorder = Recorder()
        async with assistant_lifespan(
            settings,
            sessions,
            chat_model=self.model,
            embedder=self.embedder,
            clock=lambda: EVAL_NOW,
            callbacks=[recorder],
        ) as assistant:
            assert assistant is not None  # noqa: S101 - a chat model is always passed
            transcript = await play(
                assistant,
                case,
                user_id=user.id,
                thread_id=thread.id,
                sessions=sessions,
                recorder=recorder,
                run_timeout_seconds=settings.assistant_run_timeout_seconds,
                quota_zone=quota_zone,
            )
        after = await reservation_snapshot(sessions)
        purposes = {row["purpose"]: uuid.UUID(rid) for rid, row in {**before, **after}.items()}
        seen = Observed(
            transcript=transcript,
            before=before,
            after=after,
            actions=await action_outcomes(sessions),
            names=Names(servers=servers, reservations=purposes),
        )
        score = score_case(case, seen)
        return _result(
            case, attempt, transcript, score, seen, recorder.calls, time.perf_counter() - started
        )


async def run_cases(runner: CaseRunner, cases: Sequence[Case]) -> list[CaseResult]:
    gate = asyncio.Semaphore(runner.options.concurrency)

    async def one(case: Case) -> CaseResult:
        async with gate:
            result = await runner.run(case)
            logger.info("%s %s (%s)", case.id, "pass" if result.passed else "fail", result.status)
            return result

    return list(await asyncio.gather(*(one(case) for case in cases)))


async def warm_up(model: BaseChatModel) -> float:
    """One throwaway call before anything is timed, so a cold start (or a cold prompt
    cache) is not charged to the first cases. Returns its duration."""
    started = time.perf_counter()
    await model.ainvoke([HumanMessage("Reply with the word ready.")])
    return time.perf_counter() - started
