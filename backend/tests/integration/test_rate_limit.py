import hashlib
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from app.services import passwords
from tests.integration.conftest import UNREACHABLE_HOST, ClientFactory
from tests.integration.db_utils import truncate_all

pytestmark = pytest.mark.integration

AUTH = "/api/v1/auth"
PASSWORD = "correct-horse-battery"
EMAIL = "ada@example.edu"

# Defaults from Settings.
LOGIN_IP_LIMIT = 60
LOGIN_EMAIL_FAILURE_LIMIT = 5
REGISTER_IP_LIMIT = 20


async def register(client: AsyncClient, email: str = EMAIL) -> Response:
    return await client.post(
        f"{AUTH}/register", json={"email": email, "password": PASSWORD, "full_name": "Ada"}
    )


async def login(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> Response:
    return await client.post(f"{AUTH}/login", json={"email": email, "password": password})


def assert_throttled(response: Response, slug: str, max_wait: int) -> None:
    assert response.status_code == 429, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith(f"/{slug}")
    assert 0 < int(response.headers["retry-after"]) <= max_wait


class CallCounter:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
        self.calls = 0
        for name in names:
            original = getattr(passwords, name)
            monkeypatch.setattr(passwords, name, self._wrap(original))

    def _wrap(self, original: Callable[..., Any]) -> Callable[..., Any]:
        async def counted(*args: Any) -> Any:
            self.calls += 1
            return await original(*args)

        return counted


# --- per-email failures ---


async def test_account_locks_after_repeated_failures_from_any_ip(
    client_from: ClientFactory,
) -> None:
    async with client_from("192.0.2.1") as client:
        await register(client)

    for attempt in range(LOGIN_EMAIL_FAILURE_LIMIT):
        async with client_from(f"198.51.100.{attempt + 1}") as client:
            assert (await login(client, password="wrong-password")).status_code == 401

    async with client_from("203.0.113.9") as client:
        # Even the right password is refused while the account is locked.
        response = await login(client)

    assert_throttled(response, "too-many-login-attempts", max_wait=15 * 60)


async def test_success_resets_the_failure_count(api_client: AsyncClient) -> None:
    await register(api_client)
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT - 1):
        await login(api_client, password="wrong-password")

    assert (await login(api_client)).status_code == 200

    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT - 1):
        assert (await login(api_client, password="wrong-password")).status_code == 401
    assert (await login(api_client)).status_code == 200


async def test_unknown_emails_are_throttled_too(api_client: AsyncClient) -> None:
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT):
        await login(api_client, email="nobody@example.edu")

    response = await login(api_client, email="nobody@example.edu")

    assert_throttled(response, "too-many-login-attempts", max_wait=15 * 60)


async def test_email_counter_ignores_case(api_client: AsyncClient) -> None:
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT):
        await login(api_client, email="Ada@Example.edu", password="wrong-password")

    response = await login(api_client, email="ada@example.edu")

    assert_throttled(response, "too-many-login-attempts", max_wait=15 * 60)


# --- per-IP ---


async def test_ip_is_throttled_across_emails(client_from: ClientFactory) -> None:
    async with client_from("192.0.2.50") as client:
        for attempt in range(LOGIN_IP_LIMIT):
            response = await login(client, email=f"user{attempt}@example.edu")
            assert response.status_code == 401

        response = await login(client, email="fresh@example.edu")
        assert_throttled(response, "too-many-login-attempts", max_wait=60)

    async with client_from("192.0.2.51") as other_client:
        assert (await login(other_client, email="fresh@example.edu")).status_code == 401


async def test_registration_is_throttled_per_ip(client_from: ClientFactory) -> None:
    async with client_from("192.0.2.60") as client:
        for attempt in range(REGISTER_IP_LIMIT):
            assert (await register(client, email=f"user{attempt}@example.edu")).status_code == 201

        response = await register(client, email="one-more@example.edu")
        assert_throttled(response, "too-many-requests", max_wait=60 * 60)

    async with client_from("192.0.2.61") as other_client:
        assert (await register(other_client, email="one-more@example.edu")).status_code == 201


# --- no hashing when throttled ---


async def test_throttled_login_does_no_password_work(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await register(api_client)
    for _ in range(LOGIN_EMAIL_FAILURE_LIMIT):
        await login(api_client, password="wrong-password")
    counter = CallCounter(monkeypatch, "verify_password", "verify_dummy_password", "hash_password")

    response = await login(api_client)

    assert response.status_code == 429
    assert counter.calls == 0


async def test_throttled_registration_does_no_password_work(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    for attempt in range(REGISTER_IP_LIMIT):
        await register(api_client, email=f"user{attempt}@example.edu")
    counter = CallCounter(monkeypatch, "hash_password")

    response = await register(api_client, email="one-more@example.edu")

    assert response.status_code == 429
    assert counter.calls == 0


# --- storage ---


async def test_redis_keys_hold_a_digest_not_the_email(
    api_client: AsyncClient, redis_url: str
) -> None:
    await login(api_client, email="Ada@Example.edu", password="wrong-password")

    redis = Redis.from_url(redis_url, decode_responses=True)
    keys = set(await redis.keys("*"))
    await redis.aclose()

    digest = hashlib.sha256(b"ada@example.edu").hexdigest()
    assert f"login:email:{digest}" in keys
    assert not any("ada" in key.lower() for key in keys)


# --- Redis outage ---


async def test_redis_outage_fails_open(
    make_settings: Callable[..., Settings], engine: AsyncEngine
) -> None:
    settings = make_settings(redis_url=f"redis://{UNREACHABLE_HOST}/0")
    app: FastAPI = create_app(settings)
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client,
        ):
            assert (await register(client)).status_code == 201
            assert (await login(client, password="wrong-password")).status_code == 401
            assert (await login(client)).status_code == 200
    finally:
        await truncate_all(engine)


# --- password change ---

PASSWORD_CHANGE_FAILURE_LIMIT = 5


async def change_password(client: AsyncClient, access_token: str, current: str) -> Response:
    return await client.put(
        "/api/v1/me/password",
        json={"current_password": current, "new_password": "brand-new-password"},
        headers={"Authorization": f"Bearer {access_token}"},
    )


async def test_password_change_locks_after_repeated_wrong_passwords(
    api_client: AsyncClient,
) -> None:
    access = (await register(api_client)).json()["access_token"]
    for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT):
        assert (await change_password(api_client, access, "wrong-password")).status_code == 422

    # Even the right current password is refused while locked.
    response = await change_password(api_client, access, PASSWORD)

    assert_throttled(response, "too-many-password-attempts", max_wait=15 * 60)
    assert (await login(api_client)).status_code == 200  # password unchanged


async def test_password_change_lock_survives_new_access_tokens(api_client: AsyncClient) -> None:
    """Keyed by user, so fresh tokens or another IP do not reset it."""
    access = (await register(api_client)).json()["access_token"]
    for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT):
        await change_password(api_client, access, "wrong-password")

    fresh_access = (await login(api_client)).json()["access_token"]

    response = await change_password(api_client, fresh_access, PASSWORD)
    assert_throttled(response, "too-many-password-attempts", max_wait=15 * 60)


async def test_password_change_lock_is_per_user(client_from: ClientFactory) -> None:
    async with client_from("192.0.2.70") as client:
        victim = (await register(client, email="victim@example.edu")).json()["access_token"]
        other = (await register(client, email="other@example.edu")).json()["access_token"]
        for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT):
            await change_password(client, victim, "wrong-password")

        assert (await change_password(client, other, PASSWORD)).status_code == 200


async def test_password_change_success_resets_failures(api_client: AsyncClient) -> None:
    access = (await register(api_client)).json()["access_token"]
    for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT - 1):
        await change_password(api_client, access, "wrong-password")

    changed = await change_password(api_client, access, PASSWORD)
    assert changed.status_code == 200

    new_access = changed.json()["access_token"]
    for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT - 1):
        assert (await change_password(api_client, new_access, "wrong-password")).status_code == 422


async def test_throttled_password_change_does_no_password_work(
    api_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    access = (await register(api_client)).json()["access_token"]
    for _ in range(PASSWORD_CHANGE_FAILURE_LIMIT):
        await change_password(api_client, access, "wrong-password")
    counter = CallCounter(monkeypatch, "verify_password", "hash_password")

    response = await change_password(api_client, access, PASSWORD)

    assert response.status_code == 429
    assert counter.calls == 0


async def test_assistant_limits_per_minute_and_day(
    make_settings: Callable[..., Settings], redis_url: str
) -> None:
    import uuid

    from app.core.exceptions import RateLimited
    from app.services.rate_limit import RateLimiter

    redis = Redis.from_url(redis_url)
    try:
        limiter = RateLimiter(redis, make_settings())
        user = uuid.uuid4()
        for _ in range(20):
            await limiter.check_assistant(user)
        with pytest.raises(RateLimited) as minute:
            await limiter.check_assistant(user)
        assert 0 < minute.value.retry_after_seconds <= 60

        daily = RateLimiter(
            redis, make_settings(assistant_minute_limit=1000, assistant_day_limit=3)
        )
        other = uuid.uuid4()
        for _ in range(3):
            await daily.check_assistant(other)
        with pytest.raises(RateLimited) as day:
            await daily.check_assistant(other)
        assert day.value.retry_after_seconds > 60
    finally:
        await redis.flushdb()
        await redis.aclose()


async def test_assistant_global_limit_counts_all_users_per_utc_day(
    make_settings: Callable[..., Settings], redis_url: str
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.core.exceptions import AssistantDailyLimit
    from app.services.rate_limit import RateLimiter

    redis = Redis.from_url(redis_url)
    try:
        limiter = RateLimiter(redis, make_settings(assistant_global_day_limit=3))
        evening = datetime(2030, 1, 9, 23, 0, tzinfo=UTC)
        for minute in range(3):
            await limiter.check_assistant_global(now=evening + timedelta(minutes=minute))
        with pytest.raises(AssistantDailyLimit) as over:
            await limiter.check_assistant_global(now=evening + timedelta(minutes=5))
        # Until 00:00 UTC, an hour minus five minutes away.
        assert over.value.retry_after_seconds == 55 * 60

        # A new UTC day starts a new count.
        await limiter.check_assistant_global(now=datetime(2030, 1, 10, 0, 1, tzinfo=UTC))
    finally:
        await redis.flushdb()
        await redis.aclose()
