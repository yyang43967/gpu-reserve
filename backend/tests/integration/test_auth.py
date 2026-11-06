import asyncio
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Any

import pytest
from argon2 import PasswordHasher
from httpx import AsyncClient, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_refresh_token
from app.models import RefreshToken, User, UserRole

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"
PASSWORD = "correct-horse-battery"
COOKIE = "refresh_token"


# --- helpers ---


async def register(
    client: AsyncClient,
    email: str = "ada@example.edu",
    password: str = PASSWORD,
    full_name: str = "Ada Lovelace",
) -> Response:
    return await client.post(
        f"{AUTH}/register", json={"email": email, "password": password, "full_name": full_name}
    )


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> Response:
    return await client.post(f"{AUTH}/login", json={"email": email, "password": password})


def refresh_cookie(response: Response) -> Any:
    """The parsed Set-Cookie morsel for the refresh token."""
    cookies: SimpleCookie = SimpleCookie()
    for header in response.headers.get_list("set-cookie"):
        cookies.load(header)
    return cookies[COOKIE]


async def refresh_with(client: AsyncClient, token: str) -> Response:
    """Refresh with an explicit token, bypassing the client's cookie jar."""
    client.cookies.clear()
    return await client.post(f"{AUTH}/refresh", headers={"Cookie": f"{COOKIE}={token}"})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(response: Response, status: int, slug: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith(f"/{slug}")


async def tokens_by_family(db: AsyncSession, family_id: Any) -> list[RefreshToken]:
    rows = await db.scalars(
        select(RefreshToken)
        .where(RefreshToken.family_id == family_id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def token_row(db: AsyncSession, token: str) -> RefreshToken:
    row = await db.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(token))
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


# --- register ---


async def test_register_creates_student_and_signs_in(api_client: AsyncClient) -> None:
    response = await register(api_client)

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["access_token"]
    assert body["user"]["email"] == "ada@example.edu"
    assert body["user"]["role"] == UserRole.STUDENT.value
    assert body["user"]["weekly_gpu_hour_quota"] == 48
    assert "password_hash" not in body["user"]


async def test_refresh_cookie_attributes(api_client: AsyncClient) -> None:
    cookie = refresh_cookie(await register(api_client))

    assert cookie.value
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"] == "lax"
    assert cookie["path"] == AUTH
    assert cookie["max-age"] == str(7 * 24 * 60 * 60)


async def test_register_stores_password_as_argon2id(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await register(api_client)

    user = await db_session.scalar(select(User))
    assert user is not None
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


async def test_register_normalizes_email(api_client: AsyncClient) -> None:
    response = await register(api_client, email="  Ada@Example.EDU ")

    assert response.json()["user"]["email"] == "ada@example.edu"


async def test_register_duplicate_email_differing_in_case(api_client: AsyncClient) -> None:
    await register(api_client, email="ada@example.edu")

    response = await register(api_client, email="ADA@example.edu")

    assert_problem(response, 409, "email-taken")


async def test_concurrent_registrations_of_one_email(api_client: AsyncClient) -> None:
    responses = await asyncio.gather(*(register(api_client) for _ in range(3)))

    assert sorted(r.status_code for r in responses) == [201, 409, 409]


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"email": "not-an-email", "password": PASSWORD, "full_name": "A"}, "email"),
        ({"email": "a@example.edu", "password": "short", "full_name": "A"}, "password"),
        ({"email": "a@example.edu", "password": "x" * 129, "full_name": "A"}, "password"),
        ({"email": "a@example.edu", "password": PASSWORD, "full_name": "   "}, "full_name"),
    ],
)
async def test_register_validation(
    api_client: AsyncClient, payload: dict[str, str], field: str
) -> None:
    response = await api_client.post(f"{AUTH}/register", json=payload)

    assert_problem(response, 422, "validation-failed")
    assert [error["field"] for error in response.json()["errors"]] == [field]


async def test_register_ignores_role_in_body(api_client: AsyncClient) -> None:
    response = await api_client.post(
        f"{AUTH}/register",
        json={"email": "a@example.edu", "password": PASSWORD, "full_name": "A", "role": "admin"},
    )

    assert response.json()["user"]["role"] == UserRole.STUDENT.value


# --- login ---


async def test_login_returns_tokens(api_client: AsyncClient) -> None:
    await register(api_client)

    response = await login(api_client, "ada@example.edu")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "ada@example.edu"
    assert refresh_cookie(response).value


async def test_login_is_case_insensitive(api_client: AsyncClient) -> None:
    await register(api_client, email="ada@example.edu")

    response = await login(api_client, "Ada@EXAMPLE.edu")

    assert response.status_code == 200


async def test_each_login_starts_a_new_family(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    first = refresh_cookie(await register(api_client)).value
    second = refresh_cookie(await login(api_client, "ada@example.edu")).value

    assert (await token_row(db_session, first)).family_id != (
        await token_row(db_session, second)
    ).family_id


async def test_login_failures_are_indistinguishable(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await register(api_client, email="active@example.edu")
    await register(api_client, email="inactive@example.edu")
    await db_session.execute(
        update(User).where(User.email == "inactive@example.edu").values(is_active=False)
    )
    await db_session.commit()

    wrong_password = await login(api_client, "active@example.edu", "wrong-password")
    unknown_email = await login(api_client, "nobody@example.edu")
    inactive = await login(api_client, "inactive@example.edu")

    for response in (wrong_password, unknown_email, inactive):
        assert_problem(response, 401, "invalid-credentials")
        assert COOKIE not in response.cookies
    assert wrong_password.json() == unknown_email.json() == inactive.json()


async def test_login_upgrades_outdated_password_hash(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await register(api_client)
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    await db_session.execute(update(User).values(password_hash=weak_hash))
    await db_session.commit()

    assert (await login(api_client, "ada@example.edu")).status_code == 200

    user = await db_session.scalar(select(User).execution_options(populate_existing=True))
    assert user is not None
    assert user.password_hash != weak_hash
    assert "m=19456,t=2,p=1" in user.password_hash


# --- refresh ---


async def test_refresh_rotates_the_token(api_client: AsyncClient, db_session: AsyncSession) -> None:
    registered = await register(api_client)
    original = refresh_cookie(registered).value

    response = await refresh_with(api_client, original)

    assert response.status_code == 200
    rotated = refresh_cookie(response).value
    assert rotated != original
    assert response.json()["access_token"]
    assert response.json()["user"]["email"] == "ada@example.edu"

    old_row = await token_row(db_session, original)
    new_row = await token_row(db_session, rotated)
    assert old_row.revoked_at is not None
    assert old_row.replaced_by == new_row.id
    assert new_row.family_id == old_row.family_id
    assert new_row.revoked_at is None


async def test_refresh_uses_the_cookie_jar_like_a_browser(api_client: AsyncClient) -> None:
    await register(api_client)

    first = await api_client.post(f"{AUTH}/refresh")
    second = await api_client.post(f"{AUTH}/refresh")

    assert first.status_code == 200
    assert second.status_code == 200


async def test_two_serialized_refreshes_both_succeed(api_client: AsyncClient) -> None:
    token = refresh_cookie(await register(api_client)).value

    first = await refresh_with(api_client, token)
    second = await refresh_with(api_client, refresh_cookie(first).value)

    assert first.status_code == 200
    assert second.status_code == 200


async def test_reusing_a_rotated_token_revokes_the_family(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    stolen = refresh_cookie(await register(api_client)).value
    rotated = refresh_cookie(await refresh_with(api_client, stolen)).value

    reuse = await refresh_with(api_client, stolen)

    assert_problem(reuse, 401, "refresh-token-reused")
    cleared = refresh_cookie(reuse)
    assert cleared.value == ""
    assert cleared["max-age"] == "0"
    # The legitimate holder's newer token is dead too.
    assert_problem(await refresh_with(api_client, rotated), 401, "refresh-token-reused")
    family = (await token_row(db_session, stolen)).family_id
    assert all(row.revoked_at is not None for row in await tokens_by_family(db_session, family))


async def test_reuse_does_not_touch_other_sessions(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    first_session = refresh_cookie(await register(api_client)).value
    other_session = refresh_cookie(await login(api_client, "ada@example.edu")).value
    await refresh_with(api_client, first_session)

    await refresh_with(api_client, first_session)

    assert (await refresh_with(api_client, other_session)).status_code == 200


async def test_concurrent_refreshes_of_one_token_count_as_reuse(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Without a client-side lock, two tabs refreshing at once end the session.

    The row lock lets exactly one request rotate. The other then finds the token
    revoked, which is indistinguishable from theft, so the family is revoked.
    """
    token = refresh_cookie(await register(api_client)).value
    api_client.cookies.clear()

    responses = await asyncio.gather(
        *(
            api_client.post(f"{AUTH}/refresh", headers={"Cookie": f"{COOKIE}={token}"})
            for _ in range(2)
        )
    )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 401]
    loser = next(r for r in responses if r.status_code == 401)
    assert_problem(loser, 401, "refresh-token-reused")
    family = (await token_row(db_session, token)).family_id
    rows = await tokens_by_family(db_session, family)
    assert len(rows) == 2  # exactly one rotation happened
    assert all(row.revoked_at is not None for row in rows)


async def test_refresh_without_cookie(api_client: AsyncClient) -> None:
    response = await api_client.post(f"{AUTH}/refresh")

    assert_problem(response, 401, "invalid-token")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_refresh_with_unknown_token(api_client: AsyncClient) -> None:
    response = await refresh_with(api_client, "not-a-token-we-issued")

    assert_problem(response, 401, "invalid-token")
    assert refresh_cookie(response)["max-age"] == "0"


async def test_expired_refresh_token(api_client: AsyncClient, db_session: AsyncSession) -> None:
    token = refresh_cookie(await register(api_client)).value
    await db_session.execute(
        update(RefreshToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db_session.commit()

    assert_problem(await refresh_with(api_client, token), 401, "invalid-token")


async def test_refresh_for_deactivated_user(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    token = refresh_cookie(await register(api_client)).value
    await db_session.execute(update(User).values(is_active=False))
    await db_session.commit()

    assert_problem(await refresh_with(api_client, token), 401, "invalid-token")


# --- access tokens ---


async def test_deactivated_users_access_token_stops_working(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    access = (await register(api_client)).json()["access_token"]
    await db_session.execute(update(User).values(is_active=False))
    await db_session.commit()

    response = await api_client.post(f"{AUTH}/logout", headers=bearer(access))

    assert_problem(response, 401, "invalid-token")


async def test_protected_route_requires_a_token(api_client: AsyncClient) -> None:
    response = await api_client.post(f"{AUTH}/logout")

    assert_problem(response, 401, "not-authenticated")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_protected_route_rejects_a_bad_token(api_client: AsyncClient) -> None:
    response = await api_client.post(f"{AUTH}/logout", headers=bearer("garbage"))

    assert_problem(response, 401, "invalid-token")


# --- logout ---


async def test_logout_revokes_the_family_and_clears_the_cookie(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    registered = await register(api_client)
    token = refresh_cookie(registered).value
    rotated = refresh_cookie(await refresh_with(api_client, token)).value
    api_client.cookies.set(COOKIE, rotated, domain="test", path=AUTH)

    response = await api_client.post(
        f"{AUTH}/logout", headers=bearer(registered.json()["access_token"])
    )

    assert response.status_code == 204
    assert refresh_cookie(response)["max-age"] == "0"
    family = (await token_row(db_session, token)).family_id
    assert all(row.revoked_at is not None for row in await tokens_by_family(db_session, family))
    assert (await refresh_with(api_client, rotated)).status_code == 401


async def test_logout_without_cookie_is_a_no_op(api_client: AsyncClient) -> None:
    access = (await register(api_client)).json()["access_token"]
    api_client.cookies.clear()

    response = await api_client.post(f"{AUTH}/logout", headers=bearer(access))

    assert response.status_code == 204


async def test_logout_cannot_end_another_users_session(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    victim_token = refresh_cookie(await register(api_client, email="victim@example.edu")).value
    attacker_access = (await register(api_client, email="attacker@example.edu")).json()[
        "access_token"
    ]
    api_client.cookies.clear()

    await api_client.post(
        f"{AUTH}/logout",
        headers={**bearer(attacker_access), "Cookie": f"{COOKIE}={victim_token}"},
    )

    assert (await token_row(db_session, victim_token)).revoked_at is None
