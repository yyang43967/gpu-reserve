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


class FullNameRequired(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "full-name-required"
    title = "Full name required"


class LabNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    type_slug = "lab-not-found"
    title = "Lab not found"


class LabNameTaken(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "lab-name-taken"
    title = "Lab name already in use"


class LabNotEmpty(DomainError):
    """A lab that still has servers cannot be deleted; deactivate it instead."""

    kind = ErrorKind.CONFLICT
    type_slug = "lab-not-empty"
    title = "Lab has servers"


class UnknownLab(DomainError):
    """A request body names a lab that does not exist."""

    kind = ErrorKind.INVALID
    type_slug = "unknown-lab"
    title = "Unknown lab"


class ServerNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    type_slug = "server-not-found"
    title = "Server not found"


class HostnameTaken(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "hostname-taken"
    title = "Hostname already in use"


class GpusInUse(DomainError):
    """GPUs that would be removed, or a server that would be deleted, have future
    active allocations."""

    kind = ErrorKind.CONFLICT
    type_slug = "gpus-in-use"
    title = "GPUs in use"


class InvalidUpload(DomainError):
    """An upload request or an uploaded object breaks the upload rules."""

    kind = ErrorKind.INVALID
    type_slug = "invalid-upload"
    title = "Invalid upload"


class UploadNotFound(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "upload-not-found"
    title = "Upload not found"


# --- reservations: request rules (422) ---


class InvalidTimeRange(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "invalid-time-range"
    title = "End must be after start"


class InvalidTimeGrid(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "invalid-time-grid"
    title = "Times must fall on the half hour"


class StartInPast(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "start-in-past"
    title = "Start is not in the future"


class StartTooFar(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "start-too-far"
    title = "Start is too far ahead"


class BookingTooShort(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "booking-too-short"
    title = "Booking too short"


class BookingTooLong(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "booking-too-long"
    title = "Booking too long"


class TooManyGpus(DomainError):
    """More GPUs requested than the server has."""

    kind = ErrorKind.INVALID
    type_slug = "too-many-gpus"
    title = "More GPUs than the server has"


class PeakHoursLimit(DomainError):
    kind = ErrorKind.INVALID
    type_slug = "peak-hours-limit"
    title = "Peak-hours limit exceeded"


# --- reservations: state rules (409) ---


class QuotaExceeded(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "quota-exceeded"
    title = "Weekly quota exceeded"


class ServerUnavailable(DomainError):
    """The server is in maintenance or retired, or its lab is inactive."""

    kind = ErrorKind.CONFLICT
    type_slug = "server-unavailable"
    title = "Server not available for booking"


class TooManyReservations(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "too-many-reservations"
    title = "Too many active reservations"


class GpuUnavailable(DomainError):
    kind = ErrorKind.CONFLICT
    type_slug = "gpu-unavailable"
    title = "GPUs unavailable"


class AllocationContention(DomainError):
    """Other bookings kept taking the chosen GPUs; the client may try again."""

    kind = ErrorKind.CONFLICT
    type_slug = "allocation-contention"
    title = "GPUs are in high demand"


class ReservationNotFound(DomainError):
    kind = ErrorKind.NOT_FOUND
    type_slug = "reservation-not-found"
    title = "Reservation not found"
