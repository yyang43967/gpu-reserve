import uuid
from http.cookies import SimpleCookie

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User, UserRole
from app.services import users

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"
USERS = "/api/v1/users"
PASSWORD = "correct-horse-battery"
ADMIN_EMAIL = "admin@example.edu"


async def create_user(
    db: AsyncSession, email: str, role: UserRole = UserRole.STUDENT, full_name: str = "Someone"
) -> User:
    user = await users.create_user(
        db, email=email, password=PASSWORD, full_name=full_name, role=role
    )
    await db.commit()
    return user


async def login(client: AsyncClient, email: str) -> Response:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


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


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await create_user(db_session, ADMIN_EMAIL, UserRole.ADMIN, "Grace Hopper")


@pytest.fixture
async def admin_headers(api_client: AsyncClient, admin: User) -> dict[str, str]:
    return bearer(await login(api_client, ADMIN_EMAIL))


# --- access ---


async def test_students_are_forbidden(api_client: AsyncClient, db_session: AsyncSession) -> None:
    student = await create_user(db_session, "student@example.edu")
    headers = bearer(await login(api_client, "student@example.edu"))
    some_id = student.id

    requests = [
        api_client.get(USERS, headers=headers),
        api_client.post(USERS, json={}, headers=headers),
        api_client.get(f"{USERS}/{some_id}", headers=headers),
        api_client.patch(f"{USERS}/{some_id}", json={}, headers=headers),
        api_client.delete(f"{USERS}/{some_id}", headers=headers),
    ]
    for request in requests:
        assert_problem(await request, 403, "forbidden")


# --- list ---


async def test_list_paginates_newest_first(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    for index in range(12):
        await create_user(db_session, f"user{index:02d}@example.edu")

    first = (await api_client.get(USERS, params={"page_size": 5}, headers=admin_headers)).json()
    third = (
        await api_client.get(USERS, params={"page": 3, "page_size": 5}, headers=admin_headers)
    ).json()

    assert first["total"] == 13
    assert (first["page"], first["page_size"]) == (1, 5)
    assert [u["email"] for u in first["items"]] == [
        f"user{index:02d}@example.edu" for index in (11, 10, 9, 8, 7)
    ]
    assert [u["email"] for u in third["items"]] == [
        "user01@example.edu",
        "user00@example.edu",
        ADMIN_EMAIL,
    ]


async def test_list_page_past_the_end_is_empty(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    body = (await api_client.get(USERS, params={"page": 9}, headers=admin_headers)).json()

    assert body["items"] == []
    assert body["total"] == 1


@pytest.mark.parametrize(
    "params", [{"page": 0}, {"page_size": 0}, {"page_size": 101}, {"q": "x" * 101}]
)
async def test_list_rejects_bad_paging(
    api_client: AsyncClient, admin_headers: dict[str, str], params: dict[str, object]
) -> None:
    response = await api_client.get(USERS, params=params, headers=admin_headers)

    assert_problem(response, 422, "validation-failed")


async def test_list_filters(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    await create_user(db_session, "ada@example.edu", full_name="Ada Lovelace")
    await create_user(db_session, "alan@example.edu", full_name="Alan Turing")
    inactive = await create_user(db_session, "old@example.edu", full_name="Old Account")
    inactive.is_active = False
    await db_session.commit()

    async def emails(**params: object) -> list[str]:
        response = await api_client.get(USERS, params=params, headers=admin_headers)
        return sorted(user["email"] for user in response.json()["items"])

    assert await emails(q="LOVELACE") == ["ada@example.edu"]
    assert await emails(q="ala") == ["alan@example.edu"]
    assert await emails(role="admin") == [ADMIN_EMAIL]
    assert await emails(is_active="false") == ["old@example.edu"]
    assert await emails(role="student", is_active="true") == [
        "ada@example.edu",
        "alan@example.edu",
    ]


async def test_search_treats_wildcards_literally(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    await create_user(db_session, "under_score@example.edu")
    await create_user(db_session, "underXscore@example.edu")

    response = await api_client.get(USERS, params={"q": "under_"}, headers=admin_headers)
    assert [u["email"] for u in response.json()["items"]] == ["under_score@example.edu"]

    response = await api_client.get(USERS, params={"q": "%"}, headers=admin_headers)
    assert response.json()["items"] == []


# --- create ---


async def test_create_user(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.post(
        USERS,
        json={
            "email": "New.Admin@Example.edu",
            "full_name": "New Admin",
            "password": PASSWORD,
            "role": "admin",
            "weekly_gpu_hour_quota": 100,
        },
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new.admin@example.edu"
    assert body["role"] == "admin"
    assert body["weekly_gpu_hour_quota"] == 100
    assert body["is_active"] is True
    assert (await login(api_client, "new.admin@example.edu")).status_code == 200


async def test_create_defaults_to_student(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        USERS,
        json={"email": "s@example.edu", "full_name": "S", "password": PASSWORD},
        headers=admin_headers,
    )

    assert response.json()["role"] == "student"
    assert response.json()["weekly_gpu_hour_quota"] == 48


async def test_create_requires_a_password(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        USERS, json={"email": "s@example.edu", "full_name": "S"}, headers=admin_headers
    )

    assert_problem(response, 422, "validation-failed")
    assert [e["field"] for e in response.json()["errors"]] == ["password"]


async def test_create_duplicate_email(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.post(
        USERS,
        json={"email": ADMIN_EMAIL.upper(), "full_name": "Dup", "password": PASSWORD},
        headers=admin_headers,
    )

    assert_problem(response, 409, "email-taken")


# --- get ---


async def test_get_user(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")

    response = await api_client.get(f"{USERS}/{student.id}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["email"] == "student@example.edu"


async def test_get_unknown_user(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.get(f"{USERS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "user-not-found")


async def test_get_with_malformed_id(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.get(f"{USERS}/not-a-uuid", headers=admin_headers)

    assert_problem(response, 422, "validation-failed")


# --- update ---


async def test_update_quota_keeps_sessions(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")
    session_token = refresh_token_of(await login(api_client, "student@example.edu"))

    response = await api_client.patch(
        f"{USERS}/{student.id}", json={"weekly_gpu_hour_quota": 0}, headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["weekly_gpu_hour_quota"] == 0
    assert response.json()["role"] == "student"
    assert (await refresh_with(api_client, session_token)).status_code == 200


async def test_role_change_signs_the_user_out(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")
    session_token = refresh_token_of(await login(api_client, "student@example.edu"))

    response = await api_client.patch(
        f"{USERS}/{student.id}", json={"role": "admin"}, headers=admin_headers
    )

    assert response.json()["role"] == "admin"
    assert (await refresh_with(api_client, session_token)).status_code == 401


async def test_deactivation_cuts_off_access_immediately(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")
    student_login = await login(api_client, "student@example.edu")

    response = await api_client.patch(
        f"{USERS}/{student.id}", json={"is_active": False}, headers=admin_headers
    )

    assert response.json()["is_active"] is False
    assert_problem(
        await api_client.get("/api/v1/me", headers=bearer(student_login)), 401, "invalid-token"
    )
    assert (await refresh_with(api_client, refresh_token_of(student_login))).status_code == 401
    live_tokens = await db_session.scalar(
        select(func.count())
        .select_from(RefreshToken)
        .where(RefreshToken.user_id == student.id, RefreshToken.revoked_at.is_(None))
    )
    assert live_tokens == 0


async def test_reactivation_allows_login_again(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")
    await api_client.patch(
        f"{USERS}/{student.id}", json={"is_active": False}, headers=admin_headers
    )

    await api_client.patch(f"{USERS}/{student.id}", json={"is_active": True}, headers=admin_headers)

    assert (await login(api_client, "student@example.edu")).status_code == 200


@pytest.mark.parametrize("payload", [{"role": "student"}, {"is_active": False}])
async def test_admin_cannot_demote_or_deactivate_self(
    api_client: AsyncClient, admin_headers: dict[str, str], admin: User, payload: dict[str, object]
) -> None:
    response = await api_client.patch(f"{USERS}/{admin.id}", json=payload, headers=admin_headers)

    assert_problem(response, 409, "cannot-modify-self")
    me = (await api_client.get("/api/v1/me", headers=admin_headers)).json()
    assert me["role"] == "admin"
    assert me["is_active"] is True


async def test_admin_can_change_own_quota(
    api_client: AsyncClient, admin_headers: dict[str, str], admin: User
) -> None:
    response = await api_client.patch(
        f"{USERS}/{admin.id}",
        json={"weekly_gpu_hour_quota": 10, "role": "admin", "is_active": True},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["weekly_gpu_hour_quota"] == 10


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"weekly_gpu_hour_quota": -1}, "weekly_gpu_hour_quota"),
        ({"weekly_gpu_hour_quota": 10_001}, "weekly_gpu_hour_quota"),
        ({"role": "superuser"}, "role"),
        ({"email": "x@example.edu"}, "email"),
        ({"password": "new-password-123"}, "password"),
    ],
)
async def test_update_validation(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    db_session: AsyncSession,
    payload: dict[str, object],
    field: str,
) -> None:
    student = await create_user(db_session, "student@example.edu")

    response = await api_client.patch(f"{USERS}/{student.id}", json=payload, headers=admin_headers)

    assert_problem(response, 422, "validation-failed")
    assert [e["field"] for e in response.json()["errors"]] == [field]


async def test_update_unknown_user(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.patch(
        f"{USERS}/{uuid.uuid4()}", json={"is_active": False}, headers=admin_headers
    )

    assert_problem(response, 404, "user-not-found")


# --- delete ---


async def test_delete_user_and_their_tokens(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    student = await create_user(db_session, "student@example.edu")
    await login(api_client, "student@example.edu")

    response = await api_client.delete(f"{USERS}/{student.id}", headers=admin_headers)

    assert response.status_code == 204
    assert_problem(
        await api_client.get(f"{USERS}/{student.id}", headers=admin_headers), 404, "user-not-found"
    )
    tokens = await db_session.scalar(
        select(func.count()).select_from(RefreshToken).where(RefreshToken.user_id == student.id)
    )
    assert tokens == 0


async def test_admin_cannot_delete_self(
    api_client: AsyncClient, admin_headers: dict[str, str], admin: User
) -> None:
    response = await api_client.delete(f"{USERS}/{admin.id}", headers=admin_headers)

    assert_problem(response, 409, "cannot-modify-self")


async def test_delete_unknown_user(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.delete(f"{USERS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "user-not-found")
