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
