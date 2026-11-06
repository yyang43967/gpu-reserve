import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import LabNameTaken, LabNotEmpty, LabNotFound
from app.core.unset import UNSET, Unset
from app.models import Lab, Server, User, UserRole

NAME_UNIQUE_INDEX = "uq_labs_name_lower"
SERVER_LAB_FOREIGN_KEY = "fk_servers_lab_id_labs"


@dataclass(frozen=True)
class LabListing:
    items: list[Lab]
    total: int


def can_see_inactive(viewer: User) -> bool:
    """Students only see active labs; admins see every lab."""
    return viewer.role is UserRole.ADMIN


async def list_labs(
    session: AsyncSession,
    viewer: User,
    *,
    offset: int,
    limit: int,
    is_active: bool | None = None,
) -> LabListing:
    """Labs ordered by name, ignoring case."""
    conditions: list[ColumnElement[bool]] = []
    if not can_see_inactive(viewer):
        conditions.append(Lab.is_active.is_(True))
    if is_active is not None:
        conditions.append(Lab.is_active == is_active)

    total = await session.scalar(select(func.count()).select_from(Lab).where(*conditions))
    rows = await session.scalars(
        select(Lab)
        .where(*conditions)
        .order_by(func.lower(Lab.name), Lab.id)
        .offset(offset)
        .limit(limit)
    )
    return LabListing(items=list(rows), total=total or 0)


async def get_lab(session: AsyncSession, viewer: User, lab_id: uuid.UUID) -> Lab:
    """A lab the viewer may see. Inactive labs look missing to students."""
    lab = await session.get(Lab, lab_id)
    if lab is None or (not lab.is_active and not can_see_inactive(viewer)):
        raise LabNotFound(f"No lab with id {lab_id}.")
    return lab


async def _ensure_name_free(
    session: AsyncSession, name: str, exclude_id: uuid.UUID | None = None
) -> None:
    query = select(Lab.id).where(func.lower(Lab.name) == name.lower())
    if exclude_id is not None:
        query = query.where(Lab.id != exclude_id)
    if await session.scalar(query) is not None:
        raise LabNameTaken(f"A lab named {name!r} already exists.")


async def _flush_checking_name(session: AsyncSession, name: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        # A concurrent request took the name after the check; the index decides.
        if NAME_UNIQUE_INDEX in str(exc.orig):
            raise LabNameTaken(f"A lab named {name!r} already exists.") from exc
        raise


async def create_lab(
    session: AsyncSession,
    *,
    name: str,
    location: str,
    timezone: str,
    description: str | None,
    is_active: bool,
) -> Lab:
    await _ensure_name_free(session, name)
    lab = Lab(
        name=name,
        location=location,
        timezone=timezone,
        description=description or None,
        is_active=is_active,
    )
    session.add(lab)
    await _flush_checking_name(session, name)
    await session.refresh(lab)
    return lab


async def update_lab(
    session: AsyncSession,
    actor: User,
    lab_id: uuid.UUID,
    *,
    name: str | None = None,
    location: str | None = None,
    timezone: str | None = None,
    description: str | Unset | None = UNSET,
    is_active: bool | None = None,
) -> Lab:
    """Change any of the given fields. Pass `description=None` to clear it."""
    lab = await get_lab(session, actor, lab_id)
    if name is not None and name != lab.name:
        await _ensure_name_free(session, name, exclude_id=lab.id)
        lab.name = name
    if location is not None:
        lab.location = location
    if timezone is not None:
        lab.timezone = timezone
    if description is not UNSET:
        lab.description = description or None
    if is_active is not None:
        lab.is_active = is_active
    await _flush_checking_name(session, lab.name)
    await session.refresh(lab)
    return lab


async def delete_lab(session: AsyncSession, actor: User, lab_id: uuid.UUID) -> None:
    """Delete a lab with no servers. Labs with servers are deactivated instead."""
    lab = await get_lab(session, actor, lab_id)
    if await session.scalar(select(exists().where(Server.lab_id == lab.id))):
        raise LabNotEmpty("Remove or move this lab's servers first, or deactivate the lab.")
    await session.delete(lab)
    try:
        await session.flush()
    except IntegrityError as exc:
        # A server was added after the check; the foreign key decides.
        if SERVER_LAB_FOREIGN_KEY in str(exc.orig):
            raise LabNotEmpty("Remove or move this lab's servers first.") from exc
        raise
