"""The worker's container health check, run as Compose runs it, against a real Redis."""

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from arq.constants import default_queue_name, health_check_key_suffix
from redis import Redis

from app.workers.settings import HEALTH_CHECK_INTERVAL_SECONDS, WorkerSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = (REPO_ROOT / "infra/deploy/compose.yml", REPO_ROOT / "docker-compose.yml")


def worker_healthcheck(compose_file: Path) -> list[str]:
    services = yaml.safe_load(compose_file.read_text())["services"]
    test: list[str] = services["worker"]["healthcheck"]["test"]
    return test


def arq_health_check_key() -> str:
    """The key the worker writes, derived the way arq does (Worker.__init__)."""
    queue_name = getattr(WorkerSettings, "queue_name", None) or default_queue_name
    key = getattr(WorkerSettings, "health_check_key", None)
    return str(key or queue_name + health_check_key_suffix)


def run_check(redis_url: str) -> int:
    # Compose's CMD form: the arguments after "CMD", with the image's `python`.
    command = worker_healthcheck(COMPOSE_FILES[0])[1:]
    assert command[0] == "python"
    env = {**os.environ, "REDIS_URL": redis_url}
    return subprocess.run(  # noqa: S603 - the repository's own Compose file
        [sys.executable, *command[1:]], env=env, timeout=30
    ).returncode


@pytest.fixture
def redis_client(redis_url: str) -> Iterator[Redis]:
    client = Redis.from_url(redis_url)
    client.delete(arq_health_check_key())
    yield client
    client.delete(arq_health_check_key())
    client.close()


def test_both_compose_files_use_the_same_check() -> None:
    assert worker_healthcheck(COMPOSE_FILES[0]) == worker_healthcheck(COMPOSE_FILES[1])


def test_the_check_reads_the_key_arq_writes() -> None:
    keys = re.findall(r"exists\('([^']+)'\)", " ".join(worker_healthcheck(COMPOSE_FILES[0])))
    assert keys == [arq_health_check_key()]


def test_the_check_imports_only_the_redis_client() -> None:
    # Importing the app is what made `arq --check` too slow on a throttled CPU.
    imports = re.findall(r"\bimport ([\w, ]+);", worker_healthcheck(COMPOSE_FILES[0])[-1])
    assert [{name.strip() for name in line.split(",")} for line in imports] == [
        {"os", "sys", "redis"}
    ]


def test_healthy_while_the_heartbeat_is_fresh(redis_client: Redis, redis_url: str) -> None:
    # arq's heartbeat: the key, expiring one second after the interval.
    redis_client.set(arq_health_check_key(), b"heartbeat", ex=HEALTH_CHECK_INTERVAL_SECONDS + 1)
    assert run_check(redis_url) == 0


def test_unhealthy_without_a_heartbeat(redis_client: Redis, redis_url: str) -> None:
    assert run_check(redis_url) == 1


def test_unhealthy_when_redis_is_unreachable() -> None:
    assert run_check("redis://127.0.0.1:1/0") != 0
