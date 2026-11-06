from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from app.core.config import Environment, Settings
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from tests.integration.db_utils import alembic_config, truncate_all

POSTGRES_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"

# An address nothing listens on, used to simulate an unavailable dependency.
UNREACHABLE_HOST = "127.0.0.1:1"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    with RedisContainer(REDIS_IMAGE) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def migrated_database_url(postgres_url: str) -> str:
    """The session database, migrated to head once with the real migrations."""
    command.upgrade(alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture(scope="session")
def make_settings(migrated_database_url: str, redis_url: str) -> Callable[..., Settings]:
    def factory(**overrides: object) -> Settings:
        values: dict[str, object] = {
            "environment": Environment.TEST,
            "database_url": migrated_database_url,
            "redis_url": redis_url,
            "ready_check_timeout_seconds": 1.0,
        }
        values.update(overrides)
        return Settings.model_validate(values)

    return factory


@asynccontextmanager
async def _running_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
def client_for() -> Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]:
    return _running_client


@pytest.fixture(scope="session")
async def engine(make_settings: Callable[..., Settings]) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(make_settings())
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session on the migrated database. Every table is emptied after the test."""
    async with create_session_factory(engine)() as session:
        yield session
    await truncate_all(engine)


@pytest.fixture
async def api_client(
    make_settings: Callable[..., Settings], engine: AsyncEngine
) -> AsyncIterator[AsyncClient]:
    """A client for a running app on the migrated database, emptied after the test.

    HTTPS, so the client's cookie jar honors the Secure flag like a browser would.
    """
    app: FastAPI = create_app(make_settings())
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client,
    ):
        yield client
    await truncate_all(engine)
