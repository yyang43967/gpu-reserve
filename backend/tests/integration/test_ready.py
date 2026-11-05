from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from tests.integration.conftest import UNREACHABLE_HOST

pytestmark = pytest.mark.integration

ClientFor = Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]


async def test_ready_when_database_and_redis_are_up(
    make_settings: Callable[..., Settings], client_for: ClientFor
) -> None:
    async with client_for(make_settings()) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }


async def test_not_ready_when_redis_is_unreachable(
    make_settings: Callable[..., Settings], client_for: ClientFor
) -> None:
    settings = make_settings(redis_url=f"redis://{UNREACHABLE_HOST}/0")
    async with client_for(settings) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "ok", "redis": "fail"},
    }


async def test_not_ready_when_database_is_unreachable(
    make_settings: Callable[..., Settings], client_for: ClientFor
) -> None:
    settings = make_settings(database_url=f"postgresql+asyncpg://user:pass@{UNREACHABLE_HOST}/db")
    async with client_for(settings) as client:
        response = await client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "fail", "redis": "ok"},
    }
