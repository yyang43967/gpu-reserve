"""The refresh token cookie, and the token response that sets it."""

from typing import Annotated, Final

from fastapi import Cookie, Response

from app.api import API_PREFIX
from app.core.config import Settings
from app.schemas.auth import TokenResponse
from app.schemas.user import UserRead
from app.services.auth import AuthResult

REFRESH_COOKIE_NAME: Final = "refresh_token"
# Only sent to the auth endpoints, never to the rest of the API. Endpoints elsewhere
# (such as a password change) may still set it, since Path does not restrict that.
REFRESH_COOKIE_PATH: Final = f"{API_PREFIX}/auth"

RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]


def set_refresh_cookie(response: Response, settings: Settings, refresh_token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=settings.refresh_token_ttl_days * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def token_response(response: Response, settings: Settings, result: AuthResult) -> TokenResponse:
    """Set the refresh cookie on `response` and build the body with the access token."""
    set_refresh_cookie(response, settings, result.tokens.refresh_token)
    return TokenResponse(
        access_token=result.tokens.access_token,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserRead.model_validate(result.user),
    )
