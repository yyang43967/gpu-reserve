from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
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
            # Signing needs no network, so most tests point at a host that does not exist.
            # Tests that talk to storage use the SeaweedFS container instead.
            "s3_endpoint_url": "http://storage.test:8333",
            "s3_region": "us-east-1",
            "s3_access_key_id": "test-access-key",
            "s3_secret_access_key": "test-secret-key",
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
async def clean_database(engine: AsyncEngine) -> AsyncIterator[None]:
    """Empty every table after the test.

    Fixtures that touch the database depend on this one, so it is torn down after all
    of them: no open session can hold a lock that the TRUNCATE would wait on.
    """
    yield
    await truncate_all(engine)


@pytest.fixture
async def db_session(engine: AsyncEngine, clean_database: None) -> AsyncIterator[AsyncSession]:
    """A session on the migrated database."""
    async with create_session_factory(engine)() as session:
        yield session


@pytest.fixture
async def running_app(
    make_settings: Callable[..., Settings], clean_database: None, redis_url: str
) -> AsyncIterator[FastAPI]:
    """An app with its lifespan started. Postgres and Redis are emptied afterwards."""
    app: FastAPI = create_app(make_settings())
    async with app.router.lifespan_context(app):
        yield app
    redis = Redis.from_url(redis_url)
    await redis.flushdb()
    await redis.aclose()


ClientFactory = Callable[[str], AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def client_from(running_app: FastAPI) -> ClientFactory:
    """Make a client whose requests come from the given IP address.

    HTTPS, so the client's cookie jar honors the Secure flag like a browser would.
    """

    @asynccontextmanager
    async def factory(client_ip: str) -> AsyncIterator[AsyncClient]:
        transport = ASGITransport(app=running_app, client=(client_ip, 50000))
        async with AsyncClient(transport=transport, base_url="https://test") as client:
            yield client

    return factory


@pytest.fixture
async def api_client(client_from: ClientFactory) -> AsyncIterator[AsyncClient]:
    async with client_from("192.0.2.1") as client:
        yield client
