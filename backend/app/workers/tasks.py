"""ARQ task wrappers. Each opens a transaction, calls the job in app.services.jobs with
the current time, and commits. All the logic lives in the service."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services import jobs
from app.services.email import EmailSender

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
    )
    if report != jobs.DispatchReport():
        logger.info("dispatch_notifications: %s", report)
    return report
