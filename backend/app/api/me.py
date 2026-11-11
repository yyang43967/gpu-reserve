from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from pydantic import AwareDatetime

from app.api import problem_responses
from app.api.cleanup import delete_after_commit
from app.api.deps import (
    ChangeableUser,
    CurrentUser,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    StorageDep,
)
from app.api.session_cookie import token_response
from app.schemas.auth import TokenResponse
from app.schemas.me import ChangePasswordRequest, UpdateProfileRequest
from app.schemas.reservation import QuotaRead
from app.schemas.user import UserRead
from app.services import auth, reservations, users

router = APIRouter(
    prefix="/me", tags=["me"], responses=problem_responses(status.HTTP_401_UNAUTHORIZED)
)


@router.get("")
async def get_me(user: CurrentUser, storage: StorageDep) -> UserRead:
    return UserRead.build(user, storage, datetime.now(UTC))


@router.get("/quota", responses=problem_responses(status.HTTP_422_UNPROCESSABLE_CONTENT))
async def get_quota(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    at: Annotated[
        AwareDatetime | None,
        Query(
            description=(
                "Any instant in the week to report, such as a planned booking's start. "
                "Defaults to now."
            )
        ),
    ] = None,
) -> QuotaRead:
    """GPU-hours used and left in one quota week, which runs Monday to Monday in the
    quota time zone, across all labs. A booking is checked against every week it
    overlaps, so `at` lets a client show the week a planned booking falls in."""
    quota = await reservations.quota_status(
        session, user, at=at or datetime.now(UTC), quota_zone=settings.quota_zone
    )
    return QuotaRead.build(quota)


@router.patch(
    "",
    responses=problem_responses(status.HTTP_403_FORBIDDEN, status.HTTP_422_UNPROCESSABLE_CONTENT),
)
async def update_me(
    body: UpdateProfileRequest,
    user: ChangeableUser,
    session: SessionDep,
    storage: StorageDep,
    background: BackgroundTasks,
) -> UserRead:
    """Change the name or avatar. Omitted fields stay as they are; `avatar_key: null`
    removes the avatar. A replaced avatar is deleted after the change is saved. 403 for
    the shared demo account."""
    updated = await users.update_profile(session, user, full_name=body.full_name)
    if "avatar_key" in body.model_fields_set:
        replaced = await users.set_avatar(session, storage, updated, body.avatar_key)
        delete_after_commit(background, storage, replaced)
    return UserRead.build(updated, storage, datetime.now(UTC))


@router.put(
    "/password",
    responses=problem_responses(
        status.HTTP_403_FORBIDDEN,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        status.HTTP_429_TOO_MANY_REQUESTS,
    ),
)
async def change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: ChangeableUser,
    session: SessionDep,
    settings: SettingsDep,
    storage: StorageDep,
    limiter: RateLimiterDep,
) -> TokenResponse:
    """Change the password. Signs out all other sessions and returns a fresh one. 403 for
    the shared demo account."""
    result = await auth.change_password(
        session,
        settings,
        limiter,
        user,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return token_response(response, settings, storage, result)
