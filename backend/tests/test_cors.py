from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.core.config import Settings
from app.main import CORS_ALLOWED_METHODS, create_app

FRONTEND_ORIGIN = "https://app.example.test"
PREFLIGHT_URL = "/api/v1/auth/refresh"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # Preflights are answered by the middleware alone, so no database or Redis is needed.
    app = create_app(Settings(cors_origins=[FRONTEND_ORIGIN]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def preflight(
    client: AsyncClient,
    *,
    method: str = "POST",
    headers: str | None = None,
    origin: str = FRONTEND_ORIGIN,
) -> Response:
    request_headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if headers is not None:
        request_headers["Access-Control-Request-Headers"] = headers
    return await client.options(PREFLIGHT_URL, headers=request_headers)


def allowed_headers(response: Response) -> set[str]:
    listed = response.headers["access-control-allow-headers"].split(",")
    return {header.strip().lower() for header in listed}


async def test_preflight_allows_the_headers_the_frontend_sends(client: AsyncClient) -> None:
    response = await preflight(client, headers="authorization, content-type")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert {"authorization", "content-type"} <= allowed_headers(response)


async def test_preflight_rejects_another_header(client: AsyncClient) -> None:
    response = await preflight(client, headers="authorization, x-custom-header")

    # A non-2xx preflight makes the browser refuse to send the real request.
    assert response.status_code == 400
    assert response.text == "Disallowed CORS headers"
    assert "x-custom-header" not in allowed_headers(response)


@pytest.mark.parametrize("method", CORS_ALLOWED_METHODS)
async def test_preflight_allows_each_listed_method(client: AsyncClient, method: str) -> None:
    response = await preflight(client, method=method)

    assert response.status_code == 200


async def test_preflight_rejects_an_unlisted_method(client: AsyncClient) -> None:
    response = await preflight(client, method="TRACE")

    assert response.status_code == 400


async def test_preflight_rejects_an_unknown_origin(client: AsyncClient) -> None:
    response = await preflight(client, origin="https://evil.example.test")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
