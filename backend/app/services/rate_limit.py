"""Fixed-window throttling of login, registration, password changes, upload URLs, and
assistant runs, stored in Redis.

Both endpoints run argon2, which is deliberately expensive, so unthrottled they invite
password guessing and make CPU exhaustion cheap. Limits are checked before any hashing.

If Redis is unavailable the limiter fails open: it logs a warning and lets the request
through. Sign-in keeps working during a cache outage, and the readiness probe already
reports the outage.
"""

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Final, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.exceptions import RateLimited, TooManyLoginAttempts, TooManyPasswordAttempts

logger = logging.getLogger(__name__)

# A slow Redis must not stall sign-in; past this, the limiter gives up and fails open.
REDIS_TIMEOUT_SECONDS: Final = 0.5

T = TypeVar("T")


@dataclass(frozen=True)
class Window:
    limit: int
    seconds: int


@dataclass(frozen=True)
class Count:
    value: int
    # Seconds until the window resets.
    ttl: int


def _email_digest(email: str) -> str:
    """Keys hold a digest, never the address itself. Expects a normalized email."""
    return hashlib.sha256(email.encode()).hexdigest()


class RateLimiter:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._login_ip = Window(settings.login_ip_limit, settings.login_ip_window_seconds)
        self._login_email = Window(
            settings.login_email_failure_limit, settings.login_email_window_seconds
        )
        self._register_ip = Window(settings.register_ip_limit, settings.register_ip_window_seconds)
        self._password_change = Window(
            settings.password_change_failure_limit, settings.password_change_window_seconds
        )
        self._presign = Window(settings.presign_user_limit, settings.presign_user_window_seconds)
        self._assistant = (
            Window(settings.assistant_minute_limit, 60),
            Window(settings.assistant_day_limit, 24 * 60 * 60),
        )

    async def check_login(self, *, client_ip: str, email: str) -> None:
        """Count this attempt against the IP; refuse if the IP or email is over its limit."""
        ip_count = await self._safely(self._hit(f"login:ip:{client_ip}", self._login_ip.seconds))
        if ip_count is not None and ip_count.value > self._login_ip.limit:
            raise TooManyLoginAttempts(
                ip_count.ttl, "Too many sign-in attempts from this network. Try again later."
            )

        retry_after = await self._failures_exceeded(self._email_key(email), self._login_email)
        if retry_after is not None:
            raise TooManyLoginAttempts(
                retry_after, "Too many failed sign-in attempts for this account. Try again later."
            )

    async def record_login_failure(self, email: str) -> None:
        await self._safely(self._hit(self._email_key(email), self._login_email.seconds))

    async def clear_login_failures(self, email: str) -> None:
        await self._safely(self._redis.delete(self._email_key(email)))

    async def check_register(self, *, client_ip: str) -> None:
        count = await self._safely(self._hit(f"register:ip:{client_ip}", self._register_ip.seconds))
        if count is not None and count.value > self._register_ip.limit:
            raise RateLimited(
                count.ttl, "Too many accounts created from this network. Try again later."
            )

    async def check_password_change(self, user_id: uuid.UUID) -> None:
        """Refuse when the current password was guessed wrong too often.

        Keyed by user, so a stolen access token cannot be used to brute-force the
        current password and turn temporary access into a permanent takeover.
        """
        key = self._password_change_key(user_id)
        retry_after = await self._failures_exceeded(key, self._password_change)
        if retry_after is not None:
            raise TooManyPasswordAttempts(
                retry_after, "Too many incorrect current passwords. Try again later."
            )

    async def record_password_change_failure(self, user_id: uuid.UUID) -> None:
        key = self._password_change_key(user_id)
        await self._safely(self._hit(key, self._password_change.seconds))

    async def clear_password_change_failures(self, user_id: uuid.UUID) -> None:
        await self._safely(self._redis.delete(self._password_change_key(user_id)))

    async def check_presign(self, user_id: uuid.UUID) -> None:
        """Count a request for an upload URL. Each URL lets the holder store one file,
        so without a limit one account could fill the bucket."""
        count = await self._safely(self._hit(f"presign:user:{user_id}", self._presign.seconds))
        if count is not None and count.value > self._presign.limit:
            raise RateLimited(count.ttl, "Too many upload requests. Try again later.")

    async def check_assistant(self, user_id: uuid.UUID) -> None:
        """Count one assistant run (a message or a confirmation), each of which costs
        model calls, against a per-minute and a per-day window."""
        for window in self._assistant:
            key = f"assistant:{window.seconds}:user:{user_id}"
            count = await self._safely(self._hit(key, window.seconds))
            if count is not None and count.value > window.limit:
                raise RateLimited(count.ttl, "Too many messages to the assistant. Try again later.")

    @staticmethod
    def _email_key(email: str) -> str:
        return f"login:email:{_email_digest(email)}"

    @staticmethod
    def _password_change_key(user_id: uuid.UUID) -> str:
        return f"password-change:user:{user_id}"

    async def _failures_exceeded(self, key: str, window: Window) -> int | None:
        """Seconds to wait if the failure count for `key` is at its limit, else None."""
        failures = await self._safely(self._read(key))
        if failures is not None and failures.value >= window.limit:
            return failures.ttl
        return None

    async def _hit(self, key: str, window_seconds: int) -> Count:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            # The window starts at the first hit and is not extended by later ones.
            pipe.expire(key, window_seconds, nx=True)
            pipe.ttl(key)
            value, _, ttl = await pipe.execute()
        return Count(value=int(value), ttl=max(int(ttl), 1))

    async def _read(self, key: str) -> Count:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.get(key)
            pipe.ttl(key)
            value, ttl = await pipe.execute()
        return Count(value=int(value or 0), ttl=max(int(ttl), 1))

    async def _safely(self, operation: Awaitable[T]) -> T | None:
        try:
            async with asyncio.timeout(REDIS_TIMEOUT_SECONDS):
                return await operation
        except (RedisError, OSError, TimeoutError):
            logger.warning("Rate limiter unavailable; allowing the request", exc_info=True)
            return None
