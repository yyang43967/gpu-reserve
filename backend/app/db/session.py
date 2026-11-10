from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.services.availability_cache import CACHE_INFO_KEY, AvailabilityCache


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


def create_session_factory(
    engine: AsyncEngine, *, availability_cache: AvailabilityCache | None = None
) -> async_sessionmaker[AsyncSession]:
    """Sessions with expire_on_commit off. With a cache, each session carries it in
    `info`: availability reads go through it, and commits that changed allocations
    invalidate it (see app.services.availability_cache). Without one, both go to
    Postgres only."""
    info: dict[str, Any] = {}
    if availability_cache is not None:
        info[CACHE_INFO_KEY] = availability_cache
    return async_sessionmaker(engine, expire_on_commit=False, info=info)
