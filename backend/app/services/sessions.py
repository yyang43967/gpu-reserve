"""Revocation of refresh token families (sign-in sessions)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    """End one sign-in session: every live token from the same login."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every live refresh token of a user, signing them out everywhere."""
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
