"""One logging setup for the API and the worker: JSON lines on stdout.

Each record carries the request id (API) or the job name and id (worker) from context
variables, so lines from one request or job can be found together.

What is never logged at INFO or above, by any code here: request or response bodies,
query strings, assistant message content, and reservation purposes. The access log has
the method, path, status, and duration only.
"""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO, Any, Final

from app.core.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_name_var: ContextVar[str | None] = ContextVar("job_name", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

# Loggers that would otherwise print with their own handlers and formats.
SERVER_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access", "arq")

# Third-party loggers held at WARNING whatever LOG_LEVEL says. At DEBUG, HTTP client and
# SDK loggers write request and response bodies, which hold user messages and API
# payloads; at INFO, httpx logs a line per model or embedding call.
QUIET_LOGGERS: Final = (
    "httpx",
    "httpcore",
    "openai",
    "anthropic",
    "langfuse",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "aiosmtplib",
)

# The API's own access log replaces uvicorn's, which has no duration or request id.
UVICORN_ACCESS_LOGGER: Final = "uvicorn.access"

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_RECORD_ATTRIBUTES: Final = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    # uvicorn adds a copy of the message with terminal color codes.
    | {"message", "asctime", "color_message"}
)

# Marks the handler this module installs, so reconfiguring replaces it and leaves any
# other handler (pytest's capture, for one) alone.
_HANDLER_MARK: Final = "_gpu_reserve_handler"


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever sys.stdout is when a record is emitted, not when the handler
    was made. Test runners swap stdout and close the replacement at exit; a background
    thread logging after that would otherwise hit a closed file."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> IO[str]:
        return sys.stdout

    @stream.setter
    def stream(self, value: IO[str]) -> None:
        # StreamHandler assigns the stream in __init__ and setStream; always use stdout.
        pass


class JsonFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, context, extras."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, var in (
            ("request_id", request_id_var),
            ("job", job_name_var),
            ("job_id", job_id_var),
        ):
            value = var.get()
            if value is not None:
                entry[key] = value
        for key, value in record.__dict__.items():
            if key not in _RECORD_ATTRIBUTES and not key.startswith("_") and key not in entry:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        if record.stack_info:
            entry["stack"] = record.stack_info
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(settings: Settings, stream: IO[str] | None = None) -> None:
    """Install the JSON handler on the root logger. Safe to call more than once."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)

    handler: logging.Handler = (
        logging.StreamHandler(stream) if stream is not None else _StdoutHandler()
    )
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(settings.log_level.value)

    # uvicorn and the arq command line configure these loggers with their own handlers
    # before the application starts. Send them through the root handler instead.
    for name in SERVER_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
    logging.getLogger(UVICORN_ACCESS_LOGGER).setLevel(logging.WARNING)

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
