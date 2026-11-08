import re
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

PRESIGN = "/api/v1/uploads/presign"
MIB = 1024 * 1024


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await create_user(db_session, "student@example.edu")


@pytest.fixture
async def student_headers(api_client: AsyncClient, student: User) -> dict[str, str]:
    return bearer(await login(api_client, "student@example.edu"))


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


async def test_avatar_presign(
    api_client: AsyncClient, student: User, student_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        PRESIGN,
        json={"purpose": "avatar", "content_type": "image/jpeg", "size_bytes": 300_000},
        headers=student_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert re.fullmatch(rf"avatars/{student.id}/[0-9a-f]{{32}}\.jpg", body["key"])
    assert body["url"] == "http://storage.test:8333/gpu-reserve-uploads"
    assert body["fields"]["key"] == body["key"]
    assert body["fields"]["Content-Type"] == "image/jpeg"
    assert {"policy", "x-amz-signature", "x-amz-credential"} <= set(body["fields"])
    assert body["max_bytes"] == 2 * MIB
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at.utcoffset() == timedelta(0)


async def test_server_image_presign_for_admins(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        PRESIGN,
        json={"purpose": "server_image", "content_type": "image/webp", "size_bytes": 4 * MIB},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert re.fullmatch(r"servers/[0-9a-f]{32}\.webp", response.json()["key"])
    assert response.json()["max_bytes"] == 5 * MIB


async def test_students_cannot_presign_server_images(
    api_client: AsyncClient, student_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        PRESIGN,
        json={"purpose": "server_image", "content_type": "image/png", "size_bytes": 1000},
        headers=student_headers,
    )

    assert_problem(response, 403, "forbidden")


def _body(**overrides: object) -> dict[str, object]:
    return {"purpose": "avatar", "content_type": "image/png", "size_bytes": 1000, **overrides}


@pytest.mark.parametrize(
    ("body", "slug"),
    [
        (_body(content_type="image/svg+xml"), "invalid-upload"),
        (_body(size_bytes=2 * MIB + 1), "invalid-upload"),
        (_body(size_bytes=0), "validation-failed"),
        (_body(purpose="banner"), "validation-failed"),
        (_body(key="avatars/someone-else/x.png"), "validation-failed"),
    ],
)
async def test_invalid_requests(
    api_client: AsyncClient, student_headers: dict[str, str], body: dict[str, object], slug: str
) -> None:
    response = await api_client.post(PRESIGN, json=body, headers=student_headers)

    assert_problem(response, 422, slug)


async def test_presign_is_rate_limited_per_user(
    api_client: AsyncClient, student_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    body = {"purpose": "avatar", "content_type": "image/png", "size_bytes": 1024}
    limit = 30  # the default presign_user_limit
    for _ in range(limit):
        response = await api_client.post(PRESIGN, json=body, headers=student_headers)
        assert response.status_code == 200, response.text

    response = await api_client.post(PRESIGN, json=body, headers=student_headers)

    assert_problem(response, 429, "too-many-requests")
    assert 0 < int(response.headers["retry-after"]) <= 10 * 60
    # Another account has its own window.
    other = await api_client.post(PRESIGN, json=body, headers=admin_headers)
    assert other.status_code == 200
