from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings
from app.core.errors import PROBLEM_CONTENT_TYPE, STATUS_BY_KIND
from app.core.exceptions import DomainError, ErrorKind
from app.main import create_app

BASE_URL = "https://errors.example.test"
FRONTEND_ORIGIN = "http://frontend.example.test"


def _error_class(kind: ErrorKind) -> type[DomainError]:
    return type(
        f"Sample{kind.name.title()}Error",
        (DomainError,),
        {"kind": kind, "type_slug": f"sample-{kind.value}", "title": f"Sample {kind.value}"},
    )


class Payload(BaseModel):
    email: str
    count: int = Field(ge=1)

    @field_validator("email")
    @classmethod
    def must_have_at(cls, value: str) -> str:
        if "@" not in value:
            raise ValueError("must contain @")
        return value


def _build_app() -> FastAPI:
    settings = Settings(problem_base_url=BASE_URL, cors_origins=[FRONTEND_ORIGIN])
    app = create_app(settings)

    @app.get("/test/domain/{kind}")
    async def raise_domain(kind: ErrorKind) -> None:
        raise _error_class(kind)(f"Detail for {kind.value}")

    @app.get("/test/no-detail")
    async def raise_without_detail() -> None:
        raise _error_class(ErrorKind.CONFLICT)()

    @app.post("/test/validate")
    async def validate(payload: Payload, page: int = 1) -> Payload:
        return payload

    @app.get("/test/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    return app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False so the 500 test sees the response, as a real server would.
    transport = ASGITransport(app=_build_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def test_every_error_kind_has_a_status() -> None:
    assert set(STATUS_BY_KIND) == set(ErrorKind)


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [
        (ErrorKind.NOT_FOUND, 404),
        (ErrorKind.CONFLICT, 409),
        (ErrorKind.UNAUTHENTICATED, 401),
        (ErrorKind.FORBIDDEN, 403),
        (ErrorKind.INVALID, 422),
        (ErrorKind.RATE_LIMITED, 429),
    ],
)
async def test_domain_error_becomes_problem(
    client: AsyncClient, kind: ErrorKind, expected_status: int
) -> None:
    response = await client.get(f"/test/domain/{kind.value}")

    assert response.status_code == expected_status
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json() == {
        "type": f"{BASE_URL}/sample-{kind.value}",
        "title": f"Sample {kind.value}",
        "status": expected_status,
        "detail": f"Detail for {kind.value}",
        "instance": f"/test/domain/{kind.value}",
    }


async def test_unauthenticated_sets_www_authenticate(client: AsyncClient) -> None:
    response = await client.get(f"/test/domain/{ErrorKind.UNAUTHENTICATED.value}")

    assert response.headers["www-authenticate"] == "Bearer"


async def test_missing_detail_is_omitted(client: AsyncClient) -> None:
    response = await client.get("/test/no-detail")

    assert "detail" not in response.json()


async def test_validation_error_lists_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/test/validate?page=abc", json={"email": "no-at-sign", "count": 0}
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    body = response.json()
    assert body["type"] == f"{BASE_URL}/validation-failed"
    assert body["title"] == "Validation failed"
    assert body["status"] == 422
    assert body["instance"] == "/test/validate"
    errors = {error["field"]: error["message"] for error in body["errors"]}
    assert errors["email"] == "must contain @"
    assert errors["count"].startswith("Input should be greater than or equal to 1")
    assert "page" in errors


async def test_validation_error_does_not_echo_input(client: AsyncClient) -> None:
    response = await client.post("/test/validate", json={"email": "hunter2-secret", "count": 0})

    assert "hunter2-secret" not in response.text


async def test_missing_body_is_reported(client: AsyncClient) -> None:
    response = await client.post("/test/validate")

    assert response.status_code == 422
    assert [error["field"] for error in response.json()["errors"]] == ["body"]


async def test_unknown_route_is_problem(client: AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json() == {
        "type": f"{BASE_URL}/not-found",
        "title": "Not Found",
        "status": 404,
        "instance": "/api/v1/does-not-exist",
    }


async def test_wrong_method_is_problem_with_allow_header(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["type"] == f"{BASE_URL}/method-not-allowed"
    assert response.headers["allow"] == "GET"


async def test_unhandled_error_hides_details(client: AsyncClient) -> None:
    response = await client.get("/test/crash")

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_CONTENT_TYPE
    assert response.json()["type"] == f"{BASE_URL}/internal-error"
    assert "secret internal detail" not in response.text


async def test_unhandled_error_keeps_cors_headers(client: AsyncClient) -> None:
    response = await client.get("/test/crash", headers={"Origin": FRONTEND_ORIGIN})

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
