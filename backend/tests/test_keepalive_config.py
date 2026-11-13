"""Caddy must drop an idle connection to the API before uvicorn does.

If uvicorn closes first, Caddy can send a request on a connection that is closing.
Caddy retries that only for idempotent requests, so a POST comes back as 502.
"""

import re
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "infra/deploy/compose.yml"
CADDYFILE = REPO_ROOT / "infra/deploy/Caddyfile"
UNITS = {"s": 1, "m": 60, "h": 3600}


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smh]?)", value)
    assert match, f"unrecognized duration {value!r}"
    return int(match[1]) * UNITS[match[2] or "s"]


def default(expression: str, name: str) -> str:
    """The fallback in `${NAME:-default}` (Compose) or `{$NAME:default}` (Caddyfile)."""
    match = re.search(rf"\$\{{?{name}:-?([^}}]+)\}}", expression)
    assert match, f"no default for {name} in {expression!r}"
    return match[1]


def services() -> dict[str, dict]:  # type: ignore[type-arg]
    return yaml.safe_load(COMPOSE.read_text())["services"]  # type: ignore[no-any-return]


def uvicorn_keep_alive() -> int:
    flags = [f for f in services()["api"]["command"] if f.startswith("--timeout-keep-alive=")]
    assert len(flags) == 1
    return duration_seconds(default(flags[0], "API_KEEP_ALIVE_SECONDS"))


def caddy_keep_alive_compose() -> int:
    value = services()["caddy"]["environment"]["API_UPSTREAM_KEEPALIVE"]
    return duration_seconds(default(value, "API_UPSTREAM_KEEPALIVE"))


def caddy_keep_alive_caddyfile() -> int:
    lines = [ln for ln in CADDYFILE.read_text().splitlines() if ln.strip().startswith("keepalive")]
    assert len(lines) == 1
    return duration_seconds(default(lines[0], "API_UPSTREAM_KEEPALIVE"))


def test_caddy_gives_up_on_idle_connections_before_uvicorn() -> None:
    assert caddy_keep_alive_compose() < uvicorn_keep_alive()


def test_caddyfile_fallback_matches_compose_default() -> None:
    assert caddy_keep_alive_caddyfile() == caddy_keep_alive_compose()


def test_durations_parse() -> None:
    assert duration_seconds("75") == 75
    assert duration_seconds("60s") == 60
    assert duration_seconds("2m") == 120
