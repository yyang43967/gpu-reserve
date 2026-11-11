"""When every pooled database connection stays in use, requests get a quick 503 with
Retry-After rather than waiting until they fail."""

import time
from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.config import Settings
from app.core.errors import DATABASE_BUSY_SLUG, PROBLEM_CONTENT_TYPE
from app.core.security import create_access_token
from app.main import create_app
from tests.integration.reservation_helpers import add_user

pytestmark = pytest.mark.integration

POOL_CAPACITY = 10  # pool_size 5 plus max_overflow 5, as in app.db.session
POOL_TIMEOUT = 0.3


async def test_a_request_gets_503_when_no_connection_frees_up(
    make_settings: Callable[..., Settings], db_session: AsyncSession
) -> None:
    user = await add_user(db_session, "ada@example.edu")
    settings = make_settings(database_pool_timeout_seconds=POOL_TIMEOUT)
    token, _ = create_access_token(
        user.id, secret=settings.jwt_secret.get_secret_value(), ttl=timedelta(minutes=15)
    )
    headers = {"Authorization": f"Bearer {token}"}
    app: FastAPI = create_app(settings)

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        engine: AsyncEngine = app.state.engine
        held: list[AsyncConnection] = [await engine.connect() for _ in range(POOL_CAPACITY)]
        try:
            began = time.monotonic()
            busy = await client.get("/api/v1/me", headers=headers)
            elapsed = time.monotonic() - began
        finally:
            for connection in held:
                await connection.close()
        recovered = await client.get("/api/v1/me", headers=headers)

    assert busy.status_code == 503
    assert busy.headers["Retry-After"] == str(settings.database_busy_retry_after_seconds)
    assert busy.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    body = busy.json()
    assert body["type"].endswith(f"/{DATABASE_BUSY_SLUG}")
    assert body["status"] == 503 and body["instance"] == "/api/v1/me"
    # Waited about the pool timeout, not the old 30 s.
    assert POOL_TIMEOUT <= elapsed < POOL_TIMEOUT + 2
    assert recovered.status_code == 200


def test_the_default_wait_for_a_connection_is_a_few_seconds() -> None:
    settings = Settings(jwt_secret="x" * 32)
    assert settings.database_pool_timeout_seconds == 3.0
    assert settings.database_busy_retry_after_seconds == 2
