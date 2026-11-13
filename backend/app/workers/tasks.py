"""ARQ task wrappers. Each opens a transaction, calls the job in app.services.jobs with
the current time, and commits. All the logic lives in the service."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.rag.embeddings import Embedder, EmbeddingError
from app.rag.ingest import IngestReport, format_report, ingest
from app.services import demo, demo_refresh, jobs
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


@dataclass(frozen=True)
class DemoMaintenance:
    reset: demo.DemoReset
    # None when there is no demo account, or another refresh was running.
    refresh: demo_refresh.DemoRefresh | None


async def reset_and_refresh_demo(ctx: dict[str, Any]) -> DemoMaintenance:
    """The demo's nightly maintenance, in two transactions.

    1. Return the shared demo account to its seeded state (app.services.demo), then
       clear its rate-limit counters. Counters are cleared after the commit: if the
       transaction fails, the account keeps its limits until the next run.
    2. Replace the seeded reservations with the demo plan placed around now
       (app.services.demo_refresh). The reset goes first so the bookings visitors made
       no longer count against the demo account's quota and limits when its seeded
       bookings are made again. If the refresh fails, the reset still holds and the
       previous demo data stays.

    Only deployments with a demo account run the refresh."""
    settings: Settings = ctx["settings"]
    factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        user_ids, report = await demo.reset_demo_accounts(session, now=now)
    limiter = RateLimiter(ctx["cache_redis"], settings)
    for user_id in user_ids:
        await limiter.clear_user_limits(user_id)
    if not report.accounts:
        return DemoMaintenance(reset=report, refresh=None)
    logger.info("reset_demo: %s", report)

    try:
        async with factory() as session, session.begin():
            refreshed = await demo_refresh.refresh_seeded_reservations(
                session, now=datetime.now(UTC), quota_zone=settings.quota_zone
            )
    except demo_refresh.RefreshInProgress:
        logger.info("refresh_demo skipped: another refresh is running")
        return DemoMaintenance(reset=report, refresh=None)
    logger.info(
        "refresh_demo: deleted %d, created %d, skipped %d",
        refreshed.deleted,
        refreshed.created,
        len(refreshed.skipped),
    )
    return DemoMaintenance(reset=report, refresh=refreshed)
