"""Advisory locks shared by more than one service."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_user_bookings(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Serialize a user's bookings, and changes to their quota, until the transaction
    ends.

    hashtext maps the id to 32 bits. Two users who share a hash only wait for each
    other a little; correctness does not depend on the lock being unique.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": str(user_id)}
    )
