import functools
from collections.abc import Callable, Coroutine
from typing import Any, ClassVar, Final

from arq import cron
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import configure_logging, job_id_var, job_name_var
from app.db.session import create_engine, create_session_factory
from app.rag.embeddings import build_embedder
from app.services.availability_cache import build_availability_cache
from app.services.email import build_sender
from app.workers import tasks


def with_job_name[**P, R](
    func: Callable[P, Coroutine[Any, Any, R]],
) -> Callable[P, Coroutine[Any, Any, R]]:
    """Puts the job's name on every log record the job writes. ARQ's context does not
    carry the function name, so it is set inside the job; the name ARQ registers is
    unchanged (functools.wraps keeps __qualname__)."""

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        job_name_var.set(func.__name__)
        return await func(*args, **kwargs)

    return wrapper


@with_job_name
async def ping(ctx: dict[str, Any]) -> str:
    """Trivial task used to verify the worker is consuming jobs."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = create_engine(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    # The jobs that expire and complete reservations release GPUs, so their commits
    # invalidate cached availability like any other write.
    cache_redis = Redis.from_url(str(settings.redis_url))
    ctx["cache_redis"] = cache_redis
    ctx["session_factory"] = create_session_factory(
        engine, availability_cache=build_availability_cache(settings, cache_redis)
    )
    ctx["email_sender"] = build_sender(settings)
    # None without an API key; only reindex_kb needs it, and it says so.
    ctx["embedder"] = build_embedder(settings)


async def job_start(ctx: dict[str, Any]) -> None:
    # Each job runs in its own task, so this reaches ARQ's own lines about the job and
    # everything the job logs, and nothing else.
    job_id_var.set(ctx["job_id"])


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["cache_redis"].aclose()
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
    cron(
        with_job_name(tasks.expire_pending),
        minute=EVERY_MINUTE,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
    cron(
        with_job_name(tasks.complete_finished),
        minute=EVERY_FIVE_MINUTES,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
    cron(
        with_job_name(tasks.queue_reminders),
        minute=EVERY_FIVE_MINUTES,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
    cron(
        with_job_name(tasks.dispatch_notifications),
        minute=EVERY_MINUTE,
        unique=True,
        timeout=JOB_TIMEOUT_SECONDS,
    ),
]


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, with_job_name(tasks.reindex_kb)]
    cron_jobs: ClassVar[list[Any]] = CRON_JOBS
    on_startup = startup
    on_job_start = job_start
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    # Lets `arq --check` (the container healthcheck) see a recent heartbeat.
    health_check_interval = HEALTH_CHECK_INTERVAL_SECONDS
