import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    EmailAlreadyRegistered,
    FullNameRequired,
    SelfModificationForbidden,
    UserNotFound,
)
from app.models import User, UserRole
from app.models.user import DEFAULT_WEEKLY_GPU_HOUR_QUOTA
from app.services import passwords, sessions
from app.services.storage import ObjectStorage
from app.services.uploads import UploadPurpose, verify_upload

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


async def set_avatar(
    session: AsyncSession, storage: ObjectStorage, user: User, avatar_key: str | None
) -> str | None:
    """Point the user's profile at an uploaded avatar, or remove it with None.

    Only keys under the user's own avatar prefix are accepted, and the upload is verified
    before the key is saved. Returns the key this replaced, which the caller deletes from
    storage once the transaction has committed, or None when nothing was replaced
    (including when the key did not change).
    """
    if avatar_key == user.avatar_key:
        return None
    if avatar_key is not None:
        await verify_upload(storage, avatar_key, purpose=UploadPurpose.AVATAR, owner=user)
    replaced = user.avatar_key
    user.avatar_key = avatar_key
    await session.flush()
    return replaced


# --- administration ---


@dataclass(frozen=True)
class UserListing:
    items: list[User]
    total: int


def _escape_like(term: str) -> str:
    """Treat % and _ in a search term literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_users(
    session: AsyncSession,
    *,
    offset: int,
    limit: int,
    search: str | None = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> UserListing:
    """Newest first. `search` matches part of the email or full name, ignoring case."""
    conditions = []
    if search:
        pattern = f"%{_escape_like(search.strip())}%"
        conditions.append(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
            )
        )
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)

    total = await session.scalar(select(func.count()).select_from(User).where(*conditions))
    rows = await session.scalars(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return UserListing(items=list(rows), total=total or 0)


async def get_user_or_raise(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await get_user(session, user_id)
    if user is None:
        raise UserNotFound(f"No user with id {user_id}.")
    return user


async def update_user(
    session: AsyncSession,
    actor: User,
    user_id: uuid.UUID,
    *,
    role: UserRole | None = None,
    is_active: bool | None = None,
    weekly_gpu_hour_quota: int | None = None,
) -> User:
    """Change role, active status, or quota.

    Deactivation and role changes sign the user out everywhere. Access is already
    checked against the database on every request, so this is not needed to cut off
    access; it makes the client start over with the new role.

    An admin may not demote or deactivate themselves, so the last admin cannot
    accidentally lock everyone out.
    """
    user = await get_user_or_raise(session, user_id)
    role_changes = role is not None and role != user.role
    deactivates = is_active is False and user.is_active

    if user.id == actor.id and (role_changes or deactivates):
        raise SelfModificationForbidden("You cannot demote or deactivate your own account.")

    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    if weekly_gpu_hour_quota is not None:
        user.weekly_gpu_hour_quota = weekly_gpu_hour_quota
    if role_changes or deactivates:
        await sessions.revoke_all_sessions(session, user.id)

    await session.flush()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, actor: User, user_id: uuid.UUID) -> str | None:
    """Delete an account. Its refresh tokens go with it (ON DELETE CASCADE).

    Returns the avatar key, which the caller deletes from storage once the transaction
    has committed.
    """
    if user_id == actor.id:
        raise SelfModificationForbidden("You cannot delete your own account.")
    user = await get_user_or_raise(session, user_id)
    avatar_key = user.avatar_key
    await session.delete(user)
    await session.flush()
    return avatar_key


@dataclass(frozen=True)
class AdminProvisioning:
    user: User
    created: bool


async def provision_admin(
    session: AsyncSession, *, email: str, password: str, full_name: str | None
) -> AdminProvisioning:
    """Create an admin, or make an existing account an active admin with a new password.

    Used to bootstrap the first admin and to recover a locked-out one. Resetting the
    password signs the account out everywhere, like a normal password change.
    """
    existing = await get_user_by_email(session, email)
    if existing is None:
        if not full_name or not full_name.strip():
            raise FullNameRequired("A full name is required to create a new account.")
        user = await create_user(
            session, email=email, password=password, full_name=full_name, role=UserRole.ADMIN
        )
        return AdminProvisioning(user=user, created=True)

    existing.role = UserRole.ADMIN
    existing.is_active = True
    existing.password_hash = await passwords.hash_password(password)
    if full_name and full_name.strip():
        existing.full_name = full_name.strip()
    await sessions.revoke_all_sessions(session, existing.id)
    await session.flush()
    return AdminProvisioning(user=existing, created=False)
