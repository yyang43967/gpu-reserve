from typing import Any, ClassVar, Final

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.rag.embeddings import build_embedder
from app.services.email import build_sender
from app.workers import tasks


async def ping(ctx: dict[str, Any]) -> str:
    """Trivial task used to verify the worker is consuming jobs."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["email_sender"] = build_sender(settings)
    # None without an API key; only reindex_kb needs it, and it says so.
    ctx["embedder"] = build_embedder(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["engine"].dispose()


HEALTH_CHECK_INTERVAL_SECONDS: Final = 30
EVERY_MINUTE: Final = set(range(60))
EVERY_FIVE_MINUTES: Final = set(range(0, 60, 5))
# A tick that runs longer than this is cancelled; the next tick picks up the rest.
JOB_TIMEOUT_SECONDS: Final = 50

# unique=True (the default, made explicit) gives each tick one job id, so several
# workers enqueue a tick only once. Overlap between consecutive ticks is prevented
# inside the jobs (see app.services.jobs).
CRON_JOBS: Final = [
    cron(tasks.expire_pending, minute=EVERY_MINUTE, unique=True, timeout=JOB_TIMEOUT_SECONDS),
    cron(
        tasks.complete_finished,
        minute=EVERY_FIVE_MINUTES,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
    cron(
        tasks.queue_reminders,
        minute=EVERY_FIVE_MINUTES,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
    cron(
        tasks.dispatch_notifications,
        minute=EVERY_MINUTE,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
]


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, tasks.reindex_kb]
    cron_jobs: ClassVar[list[Any]] = CRON_JOBS
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    # Lets `arq --check` (the container healthcheck) see a recent heartbeat.
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
