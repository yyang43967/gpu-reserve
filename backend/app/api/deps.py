from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import NotAuthenticated
from app.models import User
from app.services import auth

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


SettingsDep = Annotated[Settings, Depends(get_settings)]
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
