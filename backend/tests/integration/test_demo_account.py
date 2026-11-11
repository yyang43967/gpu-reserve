"""The public demo account: its credentials are served to the sign-in page, and
visitors can use it but cannot take it away from each other."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Final

import pytest
from httpx import AsyncClient, Response

from app.core.config import Settings

pytestmark = pytest.mark.integration

AUTH: Final = "/api/v1/auth"
DEMO_EMAIL: Final = "ada.lovelace@example.edu"
DEMO_PASSWORD: Final = "demo-password-shown-on-the-page"
OTHER_EMAIL: Final = "grace@example.edu"
OTHER_PASSWORD: Final = "correct-horse-battery"
# Settings default.
LOGIN_EMAIL_FAILURE_LIMIT: Final = 5


@pytest.fixture
async def demo_client(
    make_settings: Callable[..., Settings],
    client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]],
    clean_database: None,
    redis_url: str,
) -> AsyncIterator[AsyncClient]:
    from redis.asyncio import Redis

    settings = make_settings(demo_login_email=DEMO_EMAIL.upper(), demo_login_password=DEMO_PASSWORD)
    async with client_for(settings) as client:
        for email, password in ((DEMO_EMAIL, DEMO_PASSWORD), (OTHER_EMAIL, OTHER_PASSWORD)):
            response = await client.post(
                f"{AUTH}/register",
                json={"email": email, "password": password, "full_name": "Visitor"},
            )
            assert response.status_code == 201, response.text
        yield client
    redis = Redis.from_url(redis_url)
    await redis.flushdb()
    await redis.aclose()


async def _token(client: AsyncClient, email: str, password: str) -> dict[str, str]:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _forbidden_for_demo(response: Response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["type"].endswith("/demo-account-read-only")


async def test_the_sign_in_page_gets_the_demo_credentials(demo_client: AsyncClient) -> None:
    response = await demo_client.get(f"{AUTH}/demo")

    assert response.status_code == 200
    # The email as configured, normalized like every stored email.
    assert response.json() == {"email": DEMO_EMAIL, "password": DEMO_PASSWORD}


async def test_no_demo_account_answers_404(api_client: AsyncClient) -> None:
    response = await api_client.get(f"{AUTH}/demo")

    assert response.status_code == 404
    assert response.json()["type"].endswith("/demo-login-disabled")


async def test_the_demo_account_cannot_change_its_profile_password_or_avatar(
    demo_client: AsyncClient,
) -> None:
    demo = await _token(demo_client, DEMO_EMAIL, DEMO_PASSWORD)

    _forbidden_for_demo(
        await demo_client.patch("/api/v1/me", json={"full_name": "x"}, headers=demo)
    )
    _forbidden_for_demo(
        await demo_client.put(
            "/api/v1/me/password",
            json={"current_password": DEMO_PASSWORD, "new_password": "taken-over-password"},
            headers=demo,
        )
    )
    _forbidden_for_demo(
        await demo_client.post(
            "/api/v1/uploads/presign",
            json={"purpose": "avatar", "content_type": "image/png", "size_bytes": 1000},
            headers=demo,
        )
    )
    # The password still works.
    await _token(demo_client, DEMO_EMAIL, DEMO_PASSWORD)


async def test_other_accounts_can_still_change_their_profile(demo_client: AsyncClient) -> None:
    other = await _token(demo_client, OTHER_EMAIL, OTHER_PASSWORD)

    response = await demo_client.patch("/api/v1/me", json={"full_name": "Grace"}, headers=other)

    assert response.status_code == 200, response.text


async def test_failed_sign_ins_do_not_lock_the_demo_account(demo_client: AsyncClient) -> None:
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT + 2):
        wrong = await demo_client.post(
            f"{AUTH}/login", json={"email": DEMO_EMAIL, "password": "not-the-password"}
        )
        assert wrong.status_code == 401

    await _token(demo_client, DEMO_EMAIL, DEMO_PASSWORD)

    # Other accounts still lock.
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT):
        await demo_client.post(
            f"{AUTH}/login", json={"email": OTHER_EMAIL, "password": "not-the-password"}
        )
    locked = await demo_client.post(
        f"{AUTH}/login", json={"email": OTHER_EMAIL, "password": OTHER_PASSWORD}
    )
    assert locked.status_code == 429
