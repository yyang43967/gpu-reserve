from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Response, status

from app.api import problem_responses
from app.api.cleanup import delete_after_commit
from app.api.deps import CurrentUser, RateLimiterDep, SessionDep, SettingsDep, StorageDep
from app.api.session_cookie import token_response
from app.schemas.auth import TokenResponse
from app.schemas.me import ChangePasswordRequest, UpdateProfileRequest
from app.schemas.user import UserRead
from app.services import auth, users

router = APIRouter(
    prefix="/me", tags=["me"], responses=problem_responses(status.HTTP_401_UNAUTHORIZED)
)


@router.get("")
async def get_me(user: CurrentUser, storage: StorageDep) -> UserRead:
    return UserRead.build(user, storage, datetime.now(UTC))


@router.patch("", responses=problem_responses(status.HTTP_422_UNPROCESSABLE_CONTENT))
async def update_me(
    body: UpdateProfileRequest,
    user: CurrentUser,
    session: SessionDep,
    storage: StorageDep,
    background: BackgroundTasks,
) -> UserRead:
    """Change the name or avatar. Omitted fields stay as they are; `avatar_key: null`
    removes the avatar. A replaced avatar is deleted after the change is saved."""
    updated = await users.update_profile(session, user, full_name=body.full_name)
    if "avatar_key" in body.model_fields_set:
        replaced = await users.set_avatar(session, storage, updated, body.avatar_key)
        delete_after_commit(background, storage, replaced)
    return UserRead.build(updated, storage, datetime.now(UTC))


@router.put(
    "/password",
    responses=problem_responses(
        status.HTTP_422_UNPROCESSABLE_CONTENT, status.HTTP_429_TOO_MANY_REQUESTS
    ),
)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    limiter: RateLimiterDep,
) -> TokenResponse:
    """Change the password. Signs out all other sessions and returns a fresh one."""
    result = await auth.change_password(
        session,
        settings,
        limiter,
        user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return token_response(response, settings, storage, result)
