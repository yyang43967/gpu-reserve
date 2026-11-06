"""Password hashing, access tokens, and refresh token primitives.

Everything here is synchronous and free of I/O. Argon2 is CPU- and memory-heavy, so
callers on the event loop should run the password functions in a worker thread.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import cache
from typing import Final

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.exceptions import InvalidToken

PASSWORD_MIN_LENGTH: Final = 8

# Argon2id with the OWASP minimum profile: 19 MiB, 2 iterations, 1 lane. The library
# default (64 MiB, 4 lanes) costs too much memory per login on a small instance.
ARGON2_MEMORY_COST_KIB: Final = 19 * 1024
ARGON2_TIME_COST: Final = 2
ARGON2_PARALLELISM: Final = 1

ACCESS_TOKEN_ALGORITHM: Final = "HS256"  # noqa: S105 - an algorithm name, not a secret
REFRESH_TOKEN_BYTES: Final = 32

_password_hasher = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with different parameters and should be upgraded."""
    return _password_hasher.check_needs_rehash(password_hash)


@cache
def _dummy_password_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


def verify_dummy_password(password: str) -> None:
    """Spend the same time as a real verification, for logins with an unknown email.

    Without this, a fast failure would reveal that no account uses that email.
    """
    verify_password(_dummy_password_hash(), password)


class TokenType(StrEnum):
    ACCESS = "access"


def create_access_token(
    user_id: uuid.UUID, *, secret: str, ttl: timedelta, now: datetime | None = None
) -> tuple[str, datetime]:
    """Return a signed access token and its expiry time."""
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + ttl
    claims = {
        "sub": str(user_id),
        "typ": TokenType.ACCESS.value,
        "iat": issued_at,
        "exp": expires_at,
    }
    token = jwt.encode(claims, secret, algorithm=ACCESS_TOKEN_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, *, secret: str) -> uuid.UUID:
    """Validate an access token and return the user id it was issued to.

    The token proves identity only. Role and active status are read from the
    database on every request, so changes take effect immediately.
    """
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ACCESS_TOKEN_ALGORITHM],
            options={"require": ["sub", "typ", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("The access token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidToken("The access token is invalid.") from exc

    if claims["typ"] != TokenType.ACCESS.value:
        raise InvalidToken("The access token is invalid.")
    try:
        return uuid.UUID(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise InvalidToken("The access token is invalid.") from exc


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """SHA-256 is enough here: the token is 256 random bits, so it cannot be guessed."""
    return hashlib.sha256(token.encode()).hexdigest()
