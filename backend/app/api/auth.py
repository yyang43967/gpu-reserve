from fastapi import APIRouter, Request, Response, status

from app.api import problem_responses
from app.api.deps import ClientIp, CurrentUser, RateLimiterDep, SessionDep, SettingsDep
from app.api.session_cookie import RefreshCookie, clear_refresh_cookie, token_response
from app.core.errors import domain_error_response
from app.core.exceptions import InvalidToken, RefreshTokenReused
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.services import auth

router = APIRouter(prefix="/auth", tags=["auth"])

_UNAUTHORIZED = problem_responses(status.HTTP_401_UNAUTHORIZED)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    responses=problem_responses(status.HTTP_409_CONFLICT, status.HTTP_429_TOO_MANY_REQUESTS),
)
async def register(
    body: RegisterRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client_ip: ClientIp,
) -> TokenResponse:
    """Create a student account and sign it in."""
    result = await auth.register(
        session,
        settings,
        limiter,
        client_ip=client_ip,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
    )
    return token_response(response, settings, result)


@router.post(
    "/login",
    responses=problem_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_429_TOO_MANY_REQUESTS),
)
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client_ip: ClientIp,
) -> TokenResponse:
    result = await auth.login(
        session, settings, limiter, client_ip=client_ip, email=body.email, password=body.password
    )
    return token_response(response, settings, result)


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
        clear_refresh_cookie(problem, settings)
        return problem
    return token_response(response, settings, result)


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
    clear_refresh_cookie(response, settings)
    return response
