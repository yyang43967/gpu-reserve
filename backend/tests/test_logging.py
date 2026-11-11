import asyncio
import io
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import LogLevel, Settings
from app.core.logging import QUIET_LOGGERS, configure_logging
from app.core.request_context import REQUEST_ID_HEADER, RequestContextMiddleware
from app.workers.settings import job_start, with_job_name


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """The JSON handler writing to a buffer; the root logger is restored afterwards."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    stream = io.StringIO()
    configure_logging(Settings(log_level=LogLevel.INFO), stream=stream)
    yield stream
    root.handlers[:] = handlers
    root.setLevel(level)


def lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


async def endpoint(request: Request) -> PlainTextResponse:
    logging.getLogger("app.example").info("handled %s", "it", extra={"items": 3})
    return PlainTextResponse("ok")


def client() -> AsyncClient:
    app = Starlette(
        routes=[Route("/things", endpoint), Route("/api/v1/health", endpoint)],
    )
    return AsyncClient(
        transport=ASGITransport(app=RequestContextMiddleware(app)), base_url="http://test"
    )


async def test_app_records_are_json_with_the_request_id(log_stream: io.StringIO) -> None:
    async with client() as c:
        response = await c.get("/things?secret=hunter2")

    request_id = response.headers[REQUEST_ID_HEADER]
    assert len(request_id) == 32
    app_line, access_line = lines(log_stream)

    assert app_line["logger"] == "app.example"
    assert app_line["level"] == "INFO"
    assert app_line["message"] == "handled it"
    assert app_line["request_id"] == request_id
    assert app_line["items"] == 3
    assert app_line["time"].endswith("+00:00")

    assert access_line["logger"] == "app.access"
    assert access_line["request_id"] == request_id
    assert access_line["method"] == "GET"
    assert access_line["path"] == "/things"
    assert access_line["status"] == 200
    assert access_line["duration_ms"] >= 0
    # Query strings can carry user input; they are not logged.
    assert "hunter2" not in log_stream.getvalue()


async def test_a_plain_incoming_request_id_is_kept(log_stream: io.StringIO) -> None:
    async with client() as c:
        kept = await c.get("/things", headers={REQUEST_ID_HEADER: "abc-123"})
        replaced = await c.get("/things", headers={REQUEST_ID_HEADER: 'x" injected\n'})

    assert kept.headers[REQUEST_ID_HEADER] == "abc-123"
    assert replaced.headers[REQUEST_ID_HEADER] != 'x" injected\n'
    assert "injected" not in log_stream.getvalue()


async def test_probes_are_not_in_the_access_log_at_info(log_stream: io.StringIO) -> None:
    async with client() as c:
        await c.get("/api/v1/health")

    assert [line["logger"] for line in lines(log_stream)] == ["app.example"]


def test_exceptions_are_one_json_line(log_stream: io.StringIO) -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("app.example").exception("failed")

    [line] = lines(log_stream)
    assert line["level"] == "ERROR"
    assert "RuntimeError: boom" in line["exception"]


async def test_worker_records_carry_the_job_name_and_id(log_stream: io.StringIO) -> None:
    @with_job_name
    async def sweep(ctx: dict[str, Any]) -> None:
        logging.getLogger("app.workers.tasks").info("sweep changed 1 rows")

    async def run_like_arq(job_id: str) -> None:
        # ARQ runs each job in its own task: on_job_start, then the job in a child task.
        ctx = {"job_id": job_id}
        await job_start(ctx)
        logging.getLogger("arq.worker").info("starting %s", job_id)
        await asyncio.create_task(sweep(ctx))

    await asyncio.gather(run_like_arq("cron:sweep:1"), run_like_arq("cron:sweep:2"))

    by_id: dict[str, list[dict[str, Any]]] = {}
    for line in lines(log_stream):
        by_id.setdefault(line["job_id"], []).append(line)
    assert set(by_id) == {"cron:sweep:1", "cron:sweep:2"}
    for records in by_id.values():
        arq_line, job_line = records
        assert arq_line["logger"] == "arq.worker"
        assert job_line["job"] == "sweep"
        assert job_line["message"] == "sweep changed 1 rows"


def test_reconfiguring_replaces_the_handler(log_stream: io.StringIO) -> None:
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.addHandler(logging.StreamHandler(io.StringIO()))
    uvicorn_error.propagate = False

    second = io.StringIO()
    configure_logging(Settings(log_level=LogLevel.INFO), stream=second)
    uvicorn_error.info(
        "Application startup complete.", extra={"color_message": "\x1b[1mcomplete\x1b[0m"}
    )

    assert log_stream.getvalue() == ""
    [line] = lines(second)
    assert line["logger"] == "uvicorn.error"
    assert "color_message" not in line
    assert uvicorn_error.handlers == []


def test_sdk_loggers_stay_quiet_at_debug(log_stream: io.StringIO) -> None:
    configure_logging(Settings(log_level=LogLevel.DEBUG), stream=log_stream)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).debug("request body: {'content': 'private'}")
        logging.getLogger(name).info("HTTP Request: POST https://api.example.com")
    logging.getLogger("app.example").debug("visible")

    assert [line["message"] for line in lines(log_stream)] == ["visible"]


def test_log_level_is_case_insensitive() -> None:
    assert Settings(log_level="warning").log_level is LogLevel.WARNING  # type: ignore[arg-type]
