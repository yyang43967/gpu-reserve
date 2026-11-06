from fastapi import APIRouter, Response, status

from app.api import problem_responses
from app.api.deps import CurrentUser, RateLimiterDep, SessionDep, SettingsDep
from app.api.session_cookie import token_response
from app.schemas.auth import TokenResponse
from app.schemas.me import ChangePasswordRequest, UpdateProfileRequest
from app.schemas.user import UserRead
from app.services import auth, users

router = APIRouter(
    prefix="/me", tags=["me"], responses=problem_responses(status.HTTP_401_UNAUTHORIZED)
)


@router.get("")
async def get_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("")
async def update_me(body: UpdateProfileRequest, user: CurrentUser, session: SessionDep) -> UserRead:
    updated = await users.update_profile(session, user, full_name=body.full_name)
    return UserRead.model_validate(updated)


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
    return token_response(response, settings, result)
