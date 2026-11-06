"""Registration, login, and refresh token rotation with reuse detection.

Every login starts a token family. Each refresh revokes the presented token and issues
its successor in the same family. Presenting a token that was already revoked means two
parties hold copies of it (one of them likely an attacker), so the whole family is
revoked and both must sign in again.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import (
    IncorrectPassword,
    InvalidCredentials,
    InvalidToken,
    RefreshTokenReused,
)
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
    password_needs_rehash,
)
from app.models import RefreshToken, User
from app.services import passwords, users
from app.services.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


@dataclass(frozen=True)
class AuthResult:
    user: User
    tokens: IssuedTokens


def _now() -> datetime:
    return datetime.now(UTC)


async def _issue_tokens(
    session: AsyncSession, settings: Settings, user: User, family_id: uuid.UUID
) -> tuple[IssuedTokens, RefreshToken]:
    now = _now()
    access_token, access_expires_at = create_access_token(
        user.id,
        secret=settings.jwt_secret.get_secret_value(),
        ttl=timedelta(minutes=settings.access_token_ttl_minutes),
        now=now,
    )
    refresh_token = generate_refresh_token()
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        family_id=family_id,
        expires_at=now + timedelta(days=settings.refresh_token_ttl_days),
    )
    session.add(row)
    await session.flush()
    tokens = IssuedTokens(
        access_token=access_token,
        access_token_expires_at=access_expires_at,
        refresh_token=refresh_token,
        refresh_token_expires_at=row.expires_at,
    )
    return tokens, row


async def _start_session(session: AsyncSession, settings: Settings, user: User) -> AuthResult:
    tokens, _ = await _issue_tokens(session, settings, user, family_id=uuid.uuid4())
    return AuthResult(user=user, tokens=tokens)


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every live refresh token of a user, signing them out everywhere."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def register(
    session: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    *,
    client_ip: str,
    email: str,
    password: str,
    full_name: str,
) -> AuthResult:
    await limiter.check_register(client_ip=client_ip)
    user = await users.create_user(session, email=email, password=password, full_name=full_name)
    return await _start_session(session, settings, user)


async def login(
    session: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    *,
    client_ip: str,
    email: str,
    password: str,
) -> AuthResult:
    email = users.normalize_email(email)
    # Before any hashing, so throttled requests cost no argon2 work.
    await limiter.check_login(client_ip=client_ip, email=email)
    try:
        user = await _verify_credentials(session, email, password)
    except InvalidCredentials:
        await limiter.record_login_failure(email)
        raise
    await limiter.clear_login_failures(email)

    if password_needs_rehash(user.password_hash):
        user.password_hash = await passwords.hash_password(password)
    return await _start_session(session, settings, user)


async def _verify_credentials(session: AsyncSession, email: str, password: str) -> User:
    user = await users.get_user_by_email(session, email)
    if user is None:
        await passwords.verify_dummy_password(password)
        raise InvalidCredentials()
    if not await passwords.verify_password(user.password_hash, password):
        raise InvalidCredentials()
    # Checked after the password, so an inactive account answers exactly like a wrong
    # password and its existence is not revealed.
    if not user.is_active:
        raise InvalidCredentials()
    return user


async def refresh(session: AsyncSession, settings: Settings, refresh_token: str) -> AuthResult:
    """Rotate a refresh token.

    The row lock serializes concurrent refreshes of the same token: the second request
    waits, then sees the token already revoked and treats it as reuse.
    """
    row = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
        .with_for_update()
    )
    if row is None:
        raise InvalidToken("The refresh token is not recognized.")

    if row.revoked_at is not None:
        await revoke_family(session, row.family_id)
        # Commit here: the revocation must stick even though the request fails.
        await session.commit()
        logger.warning(
            "Refresh token reuse detected; revoked family %s of user %s",
            row.family_id,
            row.user_id,
        )
        raise RefreshTokenReused("This session has ended. Please sign in again.")

    if row.expires_at <= _now():
        raise InvalidToken("The refresh token has expired.")

    user = await users.get_user(session, row.user_id)
    if user is None or not user.is_active:
        raise InvalidToken("The refresh token is no longer valid.")

    row.revoked_at = _now()
    tokens, successor = await _issue_tokens(session, settings, user, family_id=row.family_id)
    row.replaced_by = successor.id
    return AuthResult(user=user, tokens=tokens)


async def change_password(
    session: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    user: User,
    *,
    current_password: str,
    new_password: str,
) -> AuthResult:
    """Change the password and sign out every other session.

    The refresh cookie is not sent to this endpoint, so the current session cannot be
    singled out. Instead all sessions are revoked and the caller gets a fresh one.
    """
    # Before any hashing, like login.
    await limiter.check_password_change(user.id)
    if not await passwords.verify_password(user.password_hash, current_password):
        await limiter.record_password_change_failure(user.id)
        raise IncorrectPassword("The current password is incorrect.")
    await limiter.clear_password_change_failures(user.id)
    user.password_hash = await passwords.hash_password(new_password)
    await revoke_all_sessions(session, user.id)
    return await _start_session(session, settings, user)


async def logout(session: AsyncSession, *, user: User, refresh_token: str | None) -> None:
    """End the session the refresh token belongs to. Idempotent."""
    if refresh_token is None:
        return
    row = await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(refresh_token))
    )
    if row is not None and row.user_id == user.id:
        await revoke_family(session, row.family_id)


async def authenticate(session: AsyncSession, settings: Settings, access_token: str) -> User:
    """Resolve an access token to an active user, read fresh from the database."""
    user_id = decode_access_token(access_token, secret=settings.jwt_secret.get_secret_value())
    user = await users.get_user(session, user_id)
    if user is None or not user.is_active:
        raise InvalidToken("The account is not active.")
    return user
