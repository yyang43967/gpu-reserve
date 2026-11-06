import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshToken, User, UserRole
from app.models.user import DEFAULT_WEEKLY_GPU_HOUR_QUOTA

pytestmark = pytest.mark.integration


def _user(email: str = "ada@example.edu", **overrides: object) -> User:
    values: dict[str, object] = {
        "email": email,
        "password_hash": "not-a-real-hash",
        "full_name": "Ada Lovelace",
    }
    values.update(overrides)
    return User(**values)


async def test_user_defaults(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.role is UserRole.STUDENT
    assert user.weekly_gpu_hour_quota == DEFAULT_WEEKLY_GPU_HOUR_QUOTA
    assert user.is_active is True
    assert user.avatar_key is None
    assert user.created_at.utcoffset() == timedelta(0)
    assert user.updated_at >= user.created_at


async def test_server_defaults_apply_to_raw_inserts(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO users (email, password_hash, full_name) "
            "VALUES ('raw@example.edu', 'x', 'Raw Insert')"
        )
    )
    row = (
        await db_session.execute(
            text("SELECT id, role, weekly_gpu_hour_quota, is_active FROM users")
        )
    ).one()

    assert isinstance(row.id, uuid.UUID)
    assert row.role == UserRole.STUDENT.value
    assert row.weekly_gpu_hour_quota == DEFAULT_WEEKLY_GPU_HOUR_QUOTA
    assert row.is_active is True


async def test_role_is_stored_as_its_value(db_session: AsyncSession) -> None:
    db_session.add(_user(role=UserRole.ADMIN))
    await db_session.commit()

    stored = await db_session.scalar(text("SELECT role::text FROM users"))
    assert stored == "admin"


async def test_email_is_unique(db_session: AsyncSession) -> None:
    db_session.add(_user())
    await db_session.commit()

    db_session.add(_user(full_name="Someone Else"))
    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.commit()


async def test_quota_cannot_be_negative(db_session: AsyncSession) -> None:
    db_session.add(_user(weekly_gpu_hour_quota=-1))
    with pytest.raises(IntegrityError, match="ck_users_quota_non_negative"):
        await db_session.commit()


async def test_deleting_user_deletes_refresh_tokens(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        RefreshToken(
            user_id=user.id,
            token_hash="a" * 64,
            family_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await db_session.commit()

    await db_session.delete(user)
    await db_session.commit()

    assert (await db_session.scalars(select(RefreshToken))).all() == []


async def test_replaced_by_is_cleared_when_successor_is_deleted(db_session: AsyncSession) -> None:
    user = _user()
    db_session.add(user)
    await db_session.flush()
    family_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    successor = RefreshToken(
        user_id=user.id, token_hash="b" * 64, family_id=family_id, expires_at=expires_at
    )
    db_session.add(successor)
    await db_session.flush()
    original = RefreshToken(
        user_id=user.id,
        token_hash="c" * 64,
        family_id=family_id,
        expires_at=expires_at,
        revoked_at=datetime.now(UTC),
        replaced_by=successor.id,
    )
    db_session.add(original)
    await db_session.commit()

    await db_session.delete(successor)
    await db_session.commit()
    await db_session.refresh(original)

    assert original.replaced_by is None
