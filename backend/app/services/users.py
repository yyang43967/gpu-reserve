import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EmailAlreadyRegistered
from app.models import User, UserRole
from app.models.user import DEFAULT_WEEKLY_GPU_HOUR_QUOTA
from app.services import passwords

EMAIL_UNIQUE_CONSTRAINT = "uq_users_email"


def normalize_email(email: str) -> str:
    """The one place emails are canonicalized. Every lookup and write goes through it."""
    return email.strip().lower()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == normalize_email(email)))


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    role: UserRole = UserRole.STUDENT,
    weekly_gpu_hour_quota: int = DEFAULT_WEEKLY_GPU_HOUR_QUOTA,
) -> User:
    email = normalize_email(email)
    if await get_user_by_email(session, email) is not None:
        raise EmailAlreadyRegistered(f"An account with {email} already exists.")

    user = User(
        email=email,
        password_hash=await passwords.hash_password(password),
        full_name=full_name.strip(),
        role=role,
        weekly_gpu_hour_quota=weekly_gpu_hour_quota,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Two registrations raced past the lookup above; the constraint decides.
        if EMAIL_UNIQUE_CONSTRAINT in str(exc.orig):
            raise EmailAlreadyRegistered(f"An account with {email} already exists.") from exc
        raise
    return user


async def update_profile(session: AsyncSession, user: User, *, full_name: str | None) -> User:
    if full_name is not None:
        user.full_name = full_name.strip()
    await session.flush()
    await session.refresh(user)
    return user
