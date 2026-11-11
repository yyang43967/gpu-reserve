from typing import Annotated, Final, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints

from app.core.security import PASSWORD_MIN_LENGTH
from app.models.user import FULL_NAME_MAX_LENGTH
from app.schemas.user import UserRead

# Argon2 accepts any length; the cap only stops oversized request bodies.
PASSWORD_MAX_LENGTH: Final = 128

NewPassword = Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
FullName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FULL_NAME_MAX_LENGTH)
]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: NewPassword
    full_name: FullName


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - a scheme name, not a secret
    # Seconds until the access token expires.
    expires_in: int
    user: UserRead


class DemoLogin(BaseModel):
    """The public demo account's credentials, shown on the sign-in page."""

    email: str
    password: str
