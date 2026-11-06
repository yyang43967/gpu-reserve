import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api import problem_responses
from app.api.deps import AdminUser, SessionDep
from app.models import UserRole
from app.schemas.admin_users import AdminUserCreate, AdminUserUpdate
from app.schemas.pagination import Page, PageParamsDep
from app.schemas.user import UserRead
from app.services import users

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
)

_NOT_FOUND = problem_responses(status.HTTP_404_NOT_FOUND)
_SEARCH_MAX_LENGTH = 100


@router.get("")
async def list_users(
    admin: AdminUser,
    session: SessionDep,
    paging: PageParamsDep,
    q: Annotated[str | None, Query(max_length=_SEARCH_MAX_LENGTH)] = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> Page[UserRead]:
    """List users, newest first. `q` matches part of the email or name."""
    listing = await users.list_users(
        session,
        offset=paging.offset,
        limit=paging.page_size,
        search=q,
        role=role,
        is_active=is_active,
    )
    return Page[UserRead](
        items=[UserRead.model_validate(user) for user in listing.items],
        total=listing.total,
        page=paging.page,
        page_size=paging.page_size,
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(status.HTTP_409_CONFLICT),
)
async def create_user(body: AdminUserCreate, admin: AdminUser, session: SessionDep) -> UserRead:
    user = await users.create_user(
        session,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        weekly_gpu_hour_quota=body.weekly_gpu_hour_quota,
    )
    await session.refresh(user)
    return UserRead.model_validate(user)


@router.get("/{user_id}", responses=_NOT_FOUND)
async def get_user(user_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await users.get_user_or_raise(session, user_id))


@router.patch(
    "/{user_id}",
    responses={**_NOT_FOUND, **problem_responses(status.HTTP_409_CONFLICT)},
)
async def update_user(
    user_id: uuid.UUID, body: AdminUserUpdate, admin: AdminUser, session: SessionDep
) -> UserRead:
    """Change role, active status, or quota. Omitted fields stay as they are."""
    user = await users.update_user(
        session,
        admin,
        user_id,
        role=body.role,
        is_active=body.is_active,
        weekly_gpu_hour_quota=body.weekly_gpu_hour_quota,
    )
    return UserRead.model_validate(user)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_NOT_FOUND, **problem_responses(status.HTTP_409_CONFLICT)},
)
async def delete_user(user_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> Response:
    await users.delete_user(session, admin, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
