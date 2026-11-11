"""Request ids and the access log.

A plain ASGI middleware rather than Starlette's BaseHTTPMiddleware, which runs the
endpoint in a separate task and would get in the way of streamed (SSE) responses.
"""

import logging
import re
import time
import uuid
from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

REQUEST_ID_HEADER: Final = "X-Request-ID"
# An incoming id is kept only if it is short and plain, so a client cannot write
# arbitrary text into the logs.
_VALID_REQUEST_ID: Final = re.compile(r"[A-Za-z0-9-]{1,64}")

# Probes from the container healthcheck and the deploy smoke test. Logged at DEBUG so
# they do not drown out real traffic.
PROBE_PATHS: Final = frozenset({"/api/v1/health", "/api/v1/ready"})

access_logger = logging.getLogger("app.access")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None


class RequestContextMiddleware:
    """Gives each HTTP request an id, returns it in X-Request-ID, and logs one access
    line when the response is complete: method, path (no query string), status, and
    duration. Bodies are never read here."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            access_logger.log(
                logging.DEBUG if path in PROBE_PATHS else logging.INFO,
                "%s %s %d %.1fms",
                scope.get("method"),
                path,
                status_code,
                duration_ms,
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
