"""Map every error the API can produce to an RFC 7807 problem response."""

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import DomainError, ErrorKind
from app.schemas.problem import FieldError, Problem, ValidationProblem

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"

STATUS_BY_KIND: Final[Mapping[ErrorKind, int]] = {
    ErrorKind.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorKind.CONFLICT: status.HTTP_409_CONFLICT,
    ErrorKind.UNAUTHENTICATED: status.HTTP_401_UNAUTHORIZED,
    ErrorKind.FORBIDDEN: status.HTTP_403_FORBIDDEN,
    ErrorKind.INVALID: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ErrorKind.RATE_LIMITED: status.HTTP_429_TOO_MANY_REQUESTS,
}

VALIDATION_SLUG: Final = "validation-failed"
INTERNAL_ERROR_SLUG: Final = "internal-error"

# Request parts FastAPI puts first in a validation error location.
_REQUEST_LOCATIONS: Final = frozenset({"body", "query", "path", "header", "cookie"})
_VALUE_ERROR_PREFIX: Final = "Value error, "


class ProblemResponse(JSONResponse):
    media_type = PROBLEM_CONTENT_TYPE


def _slug_for_status(status_code: int) -> str:
    return HTTPStatus(status_code).phrase.lower().replace(" ", "-")


def build_problem(
    *,
    base_url: str,
    status_code: int,
    slug: str,
    title: str,
    instance: str,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
    problem: Problem | None = None,
) -> ProblemResponse:
    body = problem or Problem(
        type=f"{base_url.rstrip('/')}/{slug}",
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
    )
    response_headers = dict(headers or {})
    if status_code == status.HTTP_401_UNAUTHORIZED:
        response_headers.setdefault("WWW-Authenticate", "Bearer")
    return ProblemResponse(
        body.model_dump(exclude_none=True),
        status_code=status_code,
        headers=response_headers,
    )


def _field_errors(errors: Sequence[Any]) -> list[FieldError]:
    result: list[FieldError] = []
    for error in errors:
        loc = [str(part) for part in error.get("loc", ())]
        if loc and loc[0] in _REQUEST_LOCATIONS:
            loc = loc[1:] or loc
        message = str(error.get("msg", "Invalid value"))
        message = message.removeprefix(_VALUE_ERROR_PREFIX)
        result.append(FieldError(field=".".join(loc), message=message))
    return result


class UnhandledErrorMiddleware:
    """Turn uncaught exceptions into a generic 500 problem response.

    It sits inside the CORS middleware, so even a 500 carries CORS headers and the browser
    can read it. Starlette's own server-error handler sits outside CORS.
    """

    def __init__(self, app: ASGIApp, base_url: str) -> None:
        self.app = app
        self.base_url = base_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            if response_started:
                raise
            response = build_problem(
                base_url=self.base_url,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                slug=INTERNAL_ERROR_SLUG,
                title="Internal server error",
                detail="An unexpected error occurred.",
                instance=scope.get("path", ""),
            )
            await response(scope, receive, send)


def register_error_handlers(app: FastAPI, base_url: str) -> None:
    """Install the problem-details handlers. Call before adding the CORS middleware."""

    async def handle_domain_error(request: Request, exc: Exception) -> ProblemResponse:
        assert isinstance(exc, DomainError)  # noqa: S101 - narrowed by registration
        return build_problem(
            base_url=base_url,
            status_code=STATUS_BY_KIND[exc.kind],
            slug=exc.type_slug,
            title=exc.title,
            detail=exc.detail,
            instance=request.url.path,
        )

    async def handle_validation_error(request: Request, exc: Exception) -> ProblemResponse:
        assert isinstance(exc, RequestValidationError)  # noqa: S101 - narrowed by registration
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
        problem = ValidationProblem(
            type=f"{base_url.rstrip('/')}/{VALIDATION_SLUG}",
            title="Validation failed",
            status=status_code,
            detail="The request contains invalid fields.",
            instance=request.url.path,
            errors=_field_errors(exc.errors()),
        )
        return build_problem(
            base_url=base_url,
            status_code=status_code,
            slug=VALIDATION_SLUG,
            title=problem.title,
            instance=request.url.path,
            problem=problem,
        )

    async def handle_http_exception(request: Request, exc: Exception) -> ProblemResponse:
        assert isinstance(exc, StarletteHTTPException)  # noqa: S101 - narrowed by registration
        title = HTTPStatus(exc.status_code).phrase
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
        return build_problem(
            base_url=base_url,
            status_code=exc.status_code,
            slug=_slug_for_status(exc.status_code),
            title=title,
            detail=detail,
            instance=request.url.path,
            headers=exc.headers,
        )

    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_middleware(UnhandledErrorMiddleware, base_url=base_url)
