from typing import Annotated, Final

from fastapi import APIRouter, Cookie, Request, Response, status

from app.api import API_PREFIX, problem_responses
from app.api.deps import CurrentUser, SessionDep, SettingsDep
from app.core.config import Settings
from app.core.errors import domain_error_response
from app.core.exceptions import InvalidToken, RefreshTokenReused
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserRead
from app.services import auth
from app.services.auth import AuthResult

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE_NAME: Final = "refresh_token"
# The cookie is only sent to the auth endpoints, never to the rest of the API.
REFRESH_COOKIE_PATH: Final = f"{API_PREFIX}/auth"

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def _set_refresh_cookie(response: Response, settings: Settings, result: AuthResult) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        result.tokens.refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _token_response(response: Response, settings: Settings, result: AuthResult) -> TokenResponse:
    _set_refresh_cookie(response, settings, result)
    expires_in = settings.access_token_ttl_minutes * 60
    return TokenResponse(
        access_token=result.tokens.access_token,
        expires_in=expires_in,
        user=UserRead.model_validate(result.user),
    )


_UNAUTHORIZED = problem_responses(status.HTTP_401_UNAUTHORIZED)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(status.HTTP_409_CONFLICT),
)
async def register(
    body: RegisterRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Create a student account and sign it in."""
    result = await auth.register(
        session, settings, email=body.email, password=body.password, full_name=body.full_name
    )
    return _token_response(response, settings, result)


@router.post("/login", responses=_UNAUTHORIZED)
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    result = await auth.login(session, settings, email=body.email, password=body.password)
    return _token_response(response, settings, result)


@router.post("/refresh", response_model=TokenResponse, responses=_UNAUTHORIZED)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> Response | TokenResponse:
    """Exchange the refresh cookie for a new access token and a rotated cookie."""
    try:
        if refresh_token is None:
            raise InvalidToken("No refresh token was sent.")
        result = await auth.refresh(session, settings, refresh_token)
    except (InvalidToken, RefreshTokenReused) as exc:
        # A dead cookie is useless to the client, so remove it with the error.
        problem = domain_error_response(
            exc, base_url=settings.problem_base_url, instance=request.url.path
        )
        _clear_refresh_cookie(problem, settings)
        return problem
    return _token_response(response, settings, result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_UNAUTHORIZED)
async def logout(
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> Response:
    """End this session: revoke its refresh token family and clear the cookie."""
    await auth.logout(session, user=user, refresh_token=refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response, settings)
    return response
