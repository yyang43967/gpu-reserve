from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import DemoAccountReadOnly, NotAuthenticated, PermissionDenied
from app.models import User, UserRole
from app.services import auth
from app.services.rate_limit import RateLimiter
from app.services.storage import ObjectStorage

_bearer = HTTPBearer(auto_error=False)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session and transaction per request: commit on success, roll back on error."""
    async with request.app.state.session_factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        await session.commit()


def get_rate_limiter(request: Request) -> RateLimiter:
    return RateLimiter(request.app.state.redis, request.app.state.settings)


def get_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_client_ip(request: Request) -> str:
    """The peer address. Behind the reverse proxy, uvicorn's --proxy-headers sets it from
    X-Forwarded-For, trusting only the addresses in FORWARDED_ALLOW_IPS."""
    return request.client.host if request.client else "unknown"


SettingsDep = Annotated[Settings, Depends(get_settings)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ClientIp = Annotated[str, Depends(get_client_ip)]
# scope="function" commits before the response is sent, so a client never sees success
# for a change that then fails to commit.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise NotAuthenticated("Send an access token in the Authorization header.")
    return await auth.authenticate(session, settings, credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role is not UserRole.ADMIN:
        raise PermissionDenied("This action requires an administrator.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def require_not_demo(user: CurrentUser) -> User:
    """Refuse changes to the shared demo account's own profile and password, which
    every visitor signs in to."""
    if user.is_demo:
        raise DemoAccountReadOnly("The demo account's profile and password cannot be changed.")
    return user


ChangeableUser = Annotated[User, Depends(require_not_demo)]
