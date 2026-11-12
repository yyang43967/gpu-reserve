"""ARQ task wrappers. Each opens a transaction, calls the job in app.services.jobs with
the current time, and commits. All the logic lives in the service."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import Embedder, EmbeddingError
from app.rag.ingest import IngestReport, format_report, ingest
from app.services import demo, jobs
from app.services.email import EmailSender
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

SweepJob = Callable[..., Awaitable[int | None]]


async def _sweep(ctx: dict[str, Any], job: jobs.Job, run: SweepJob) -> int | None:
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    async with factory() as session, session.begin():
        changed = await run(session, now=datetime.now(UTC))
    if changed is None:
        logger.info("%s skipped: another run holds its lock", job.value)
    elif changed:
        logger.info("%s changed %d rows", job.value, changed)
    return changed


async def expire_pending(ctx: dict[str, Any]) -> int | None:
    return await _sweep(ctx, jobs.Job.EXPIRE_PENDING, jobs.expire_pending)


async def complete_finished(ctx: dict[str, Any]) -> int | None:
    return await _sweep(ctx, jobs.Job.COMPLETE_FINISHED, jobs.complete_finished)


async def queue_reminders(ctx: dict[str, Any]) -> int | None:
    return await _sweep(ctx, jobs.Job.QUEUE_REMINDERS, jobs.queue_reminders)


async def dispatch_notifications(ctx: dict[str, Any]) -> jobs.DispatchReport:
    settings: Settings = ctx["settings"]
    sender: EmailSender = ctx["email_sender"]
    report = await jobs.dispatch_notifications(
        ctx["session_factory"],
        sender,
        now=datetime.now(UTC),
        frontend_url=settings.frontend_url,
        suppressed_domains=frozenset(settings.email_suppressed_domains),
    )
    if report != jobs.DispatchReport():
        logger.info("dispatch_notifications: %s", report)
    return report


async def reindex_kb(ctx: dict[str, Any]) -> IngestReport:
    """Bring the stored knowledge base in line with the documents. Run on demand."""
    settings: Settings = ctx["settings"]
    embedder: Embedder | None = ctx["embedder"]
    if embedder is None:
        raise EmbeddingError("OPENAI_API_KEY is not set, so the knowledge base cannot be indexed")
    report = await ingest(ctx["session_factory"], embedder, settings.kb_dir)
    logger.info("reindex_kb: %s", format_report(report))
    return report


async def reset_demo(ctx: dict[str, Any]) -> demo.DemoReset:
    """Return the shared demo account to its seeded state (app.services.demo), then
    clear its rate-limit counters. Counters are cleared after the commit: if the
    transaction fails, the account keeps its limits until the next run."""
    settings: Settings = ctx["settings"]
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    async with factory() as session, session.begin():
        user_ids, report = await demo.reset_demo_accounts(session, now=datetime.now(UTC))
    limiter = RateLimiter(ctx["cache_redis"], settings)
    for user_id in user_ids:
        await limiter.clear_user_limits(user_id)
    if report.accounts:
        logger.info("reset_demo: %s", report)
    return report
