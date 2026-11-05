import asyncio
import logging
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas.health import CheckResult, ReadinessChecks, ReadinessResponse, ReadinessStatus

logger = logging.getLogger(__name__)


async def _run_check(
    name: str, check: Callable[[], Awaitable[object]], timeout_seconds: float
) -> CheckResult:
    try:
        async with asyncio.timeout(timeout_seconds):
            await check()
    except Exception:
        logger.warning("Readiness check failed: %s", name, exc_info=True)
        return CheckResult.FAIL
    return CheckResult.OK


async def check_readiness(
    engine: AsyncEngine, redis: Redis, timeout_seconds: float
) -> ReadinessResponse:
    """Check concurrently that the database and Redis each answer within the timeout."""

    async def ping_database() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    database, cache = await asyncio.gather(
        _run_check("database", ping_database, timeout_seconds),
        _run_check("redis", redis.ping, timeout_seconds),
    )
    ready = database is CheckResult.OK and cache is CheckResult.OK
    return ReadinessResponse(
        status=ReadinessStatus.READY if ready else ReadinessStatus.NOT_READY,
        checks=ReadinessChecks(database=database, redis=cache),
    )
