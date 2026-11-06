"""Domain exceptions raised by services.

Nothing here knows about HTTP. Each error declares a `kind`, and the API layer maps
kinds to status codes in one place (`app.core.errors`).
"""

from enum import StrEnum
from typing import ClassVar


class ErrorKind(StrEnum):
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"


class DomainError(Exception):
    """Base class for expected, user-facing errors.

    Subclasses set `kind`, a stable `type_slug` used in the problem `type` URI, and a short
    human-readable `title`. `detail` explains this particular occurrence.
    """

    kind: ClassVar[ErrorKind]
    type_slug: ClassVar[str]
    title: ClassVar[str]

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail


class InvalidToken(DomainError):
    kind = ErrorKind.UNAUTHENTICATED
    type_slug = "invalid-token"
    title = "Invalid token"


class NotAuthenticated(DomainError):
    kind = ErrorKind.UNAUTHENTICATED
    type_slug = "not-authenticated"
    title = "Authentication required"


class InvalidCredentials(DomainError):
    kind = ErrorKind.UNAUTHENTICATED
    type_slug = "invalid-credentials"
    title = "Invalid email or password"


class RefreshTokenReused(DomainError):
    kind = ErrorKind.UNAUTHENTICATED
    type_slug = "refresh-token-reused"
    title = "Refresh token reused"


class EmailAlreadyRegistered(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "email-taken"
    title = "Email already registered"


class RateLimited(DomainError):
    """Base for throttling errors. Carries how long the client should wait."""

    kind = ErrorKind.RATE_LIMITED
    type_slug = "too-many-requests"
    title = "Too many requests"

    def __init__(self, retry_after_seconds: int, detail: str | None = None) -> None:
        super().__init__(detail)
        self.retry_after_seconds = retry_after_seconds


class TooManyLoginAttempts(RateLimited):
    type_slug = "too-many-login-attempts"
    title = "Too many login attempts"


class TooManyPasswordAttempts(RateLimited):
    type_slug = "too-many-password-attempts"
    title = "Too many password attempts"


class IncorrectPassword(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "incorrect-password"
    title = "Incorrect password"


class PermissionDenied(DomainError):
    kind = ErrorKind.FORBIDDEN
    type_slug = "forbidden"
    title = "Permission denied"


class UserNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    type_slug = "user-not-found"
    title = "User not found"


class SelfModificationForbidden(DomainError):
    """An admin tried to delete, deactivate, or demote their own account."""

    kind = ErrorKind.CONFLICT
    type_slug = "cannot-modify-self"
    title = "Cannot modify own account"
