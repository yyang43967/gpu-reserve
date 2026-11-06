from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, UserRole

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"
ME = "/api/v1/me"
PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "new-staple-battery"
EMAIL = "ada@example.edu"


async def register(client: AsyncClient) -> Response:
    return await client.post(
        f"{AUTH}/register",
        json={"email": EMAIL, "password": PASSWORD, "full_name": "Ada Lovelace"},
    )


def bearer(response: Response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def refresh_token_of(response: Response) -> str:
    cookies: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return cookies["refresh_token"].value


async def refresh_with(client: AsyncClient, token: str) -> Response:
    client.cookies.clear()
    return await client.post(f"{AUTH}/refresh", headers={"Cookie": f"refresh_token={token}"})


def assert_problem(response: Response, status: int, slug: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["type"].endswith(f"/{slug}")


# --- GET /me ---


async def test_get_me(api_client: AsyncClient) -> None:
    headers = bearer(await register(api_client))

    response = await api_client.get(ME, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == EMAIL
    assert body["full_name"] == "Ada Lovelace"
    assert body["role"] == UserRole.STUDENT.value
    assert "password_hash" not in body


async def test_get_me_requires_auth(api_client: AsyncClient) -> None:
    assert_problem(await api_client.get(ME), 401, "not-authenticated")


# --- PATCH /me ---


async def test_update_full_name(api_client: AsyncClient) -> None:
    headers = bearer(await register(api_client))

    response = await api_client.patch(ME, json={"full_name": "  Ada King  "}, headers=headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "Ada King"
    assert (await api_client.get(ME, headers=headers)).json()["full_name"] == "Ada King"


async def test_empty_patch_changes_nothing(api_client: AsyncClient) -> None:
    headers = bearer(await register(api_client))

    response = await api_client.patch(ME, json={}, headers=headers)

    assert response.status_code == 200
    assert response.json()["full_name"] == "Ada Lovelace"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"full_name": "   "}, "full_name"),
        ({"role": "admin"}, "role"),
        ({"weekly_gpu_hour_quota": 1000}, "weekly_gpu_hour_quota"),
        ({"email": "other@example.edu"}, "email"),
        ({"avatar_key": "avatars/x.png"}, "avatar_key"),
    ],
)
async def test_update_rejects_invalid_or_privileged_fields(
    api_client: AsyncClient, payload: dict[str, str], field: str
) -> None:
    headers = bearer(await register(api_client))

    response = await api_client.patch(ME, json=payload, headers=headers)

    assert_problem(response, 422, "validation-failed")
    assert [error["field"] for error in response.json()["errors"]] == [field]
    me = (await api_client.get(ME, headers=headers)).json()
    assert me["role"] == UserRole.STUDENT.value
    assert me["weekly_gpu_hour_quota"] == 48


# --- PUT /me/password ---


async def change_password(
    client: AsyncClient, headers: dict[str, str], current: str = PASSWORD, new: str = NEW_PASSWORD
) -> Response:
    return await client.put(
        f"{ME}/password",
        json={"current_password": current, "new_password": new},
        headers=headers,
    )


async def login(client: AsyncClient, password: str) -> Response:
    return await client.post(f"{AUTH}/login", json={"email": EMAIL, "password": password})


async def test_change_password(api_client: AsyncClient) -> None:
    headers = bearer(await register(api_client))

    response = await change_password(api_client, headers)

    assert response.status_code == 200
    assert response.json()["access_token"]
    assert (await login(api_client, NEW_PASSWORD)).status_code == 200
    assert_problem(await login(api_client, PASSWORD), 401, "invalid-credentials")


async def test_change_password_signs_out_other_sessions(api_client: AsyncClient) -> None:
    registered = await register(api_client)
    first_session = refresh_token_of(registered)
    second_session = refresh_token_of(await login(api_client, PASSWORD))

    response = await change_password(api_client, bearer(registered))

    for old_token in (first_session, second_session):
        assert (await refresh_with(api_client, old_token)).status_code == 401
    # The fresh session issued by the password change works.
    assert (await refresh_with(api_client, refresh_token_of(response))).status_code == 200


async def test_change_password_sets_cookie_for_auth_path(api_client: AsyncClient) -> None:
    response = await change_password(api_client, bearer(await register(api_client)))

    cookies: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    assert cookies["refresh_token"]["path"] == AUTH
    assert cookies["refresh_token"]["httponly"] is True


async def test_wrong_current_password_changes_nothing(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    registered = await register(api_client)

    response = await change_password(api_client, bearer(registered), current="wrong-password")

    assert_problem(response, 422, "incorrect-password")
    assert (await login(api_client, PASSWORD)).status_code == 200
    tokens = await db_session.scalars(select(RefreshToken))
    # The registration token and the login above, neither revoked.
    assert [token.revoked_at for token in tokens] == [None, None]


@pytest.mark.parametrize(
    ("current", "new", "field"),
    [
        (PASSWORD, "short", "new_password"),
        (PASSWORD, PASSWORD, "new_password"),
        ("", NEW_PASSWORD, "current_password"),
    ],
)
async def test_change_password_validation(
    api_client: AsyncClient, current: str, new: str, field: str
) -> None:
    headers = bearer(await register(api_client))

    response = await change_password(api_client, headers, current=current, new=new)

    assert_problem(response, 422, "validation-failed")
    assert [error["field"] for error in response.json()["errors"]] == [field]


async def test_change_password_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.put(
        f"{ME}/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert_problem(response, 401, "not-authenticated")
