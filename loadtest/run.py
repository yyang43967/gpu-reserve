#!/usr/bin/env python3
"""Run the load tests against docker-compose.loadtest.yml and keep every result.

    python3 loadtest/run.py                          # everything, 3 repeats
    python3 loadtest/run.py --scripts availability --repeats 1 --warmup 10s --measure 20s

For each repeat, the cache-off and cache-on configurations alternate, so slow drift on
the host (thermal, background work) affects both alike. Before every script run the
database is reset to the same seed and the cache is emptied. During each run, a sampler
records the CPU of the API and Postgres containers and Postgres's connections and
commits once a second.

The cache hit rate is measured in separate runs with counting turned on, because
counting adds a Redis round trip to every read; those runs' latencies are not reported.

Results go to loadtest/out/<UTC time>/ (ignored by Git); `report.py` summarizes them.
Standard library only, so it runs with the system python3. Needs docker and k6.
"""
# A host script that drives docker and k6 found on PATH, with fixed arguments.
# ruff: noqa: S603, S607

import argparse
import contextlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "loadtest"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.loadtest.yml")]
BASE_URL = "http://127.0.0.1:18100"
TIMED_SCRIPTS = ("browse_servers", "availability", "slot_search")
ALL_SCRIPTS = (*TIMED_SCRIPTS, "booking_race")
# Scripts that read through the cache, and so get a hit-rate run.
CACHED_SCRIPTS = ("availability", "slot_search")
CONFIGS = ("cache-off", "cache-on")
K6_ABORTED_BY_THRESHOLD = 99

# The API's connections as Postgres sees them, excluding the sampler's own: running a
# statement, holding a transaction open between statements, and open at all.
PG_SAMPLE = (
    "SELECT"
    " count(*) FILTER (WHERE state = 'active'),"
    " count(*) FILTER (WHERE state LIKE 'idle in transaction%'),"
    " count(*),"
    " (SELECT xact_commit FROM pg_stat_database WHERE datname = 'gpureserve')"
    " FROM pg_stat_activity"
    " WHERE datname = 'gpureserve' AND backend_type = 'client backend'"
    " AND pid <> pg_backend_pid();"
)
PG_SAMPLE_INTERVAL = 0.25


class RunFailed(Exception):
    pass


def seconds(duration: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m)", duration)
    if not match:
        raise argparse.ArgumentTypeError("use a duration like 30s or 2m")
    value, unit = int(match[1]), match[2]
    return value * 60 if unit == "m" else value


def compose(*args: str, env: dict[str, str] | None = None, capture: bool = True) -> str:
    result = subprocess.run(
        [*COMPOSE, *args],
        env={**_base_env(), **(env or {})},
        capture_output=capture,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-5:]
        raise RunFailed(f"docker compose {' '.join(args)} failed: {' / '.join(detail)}")
    return result.stdout if capture else ""


def _base_env() -> dict[str, str]:
    return dict(os.environ)


def configure(config: str, *, stats: bool = False) -> None:
    """Start the stack with the cache on or off, and confirm the API got the setting."""
    enabled = "true" if config == "cache-on" else "false"
    env = {"AVAILABILITY_CACHE_ENABLED": enabled, "AVAILABILITY_CACHE_STATS": str(stats).lower()}
    compose("up", "-d", "--wait", env=env)
    # A new volume starts empty; on later runs this finds nothing to do.
    compose("exec", "-T", "api", "alembic", "upgrade", "head", env=env)
    container = compose("ps", "-q", "api").strip()
    inspected = subprocess.run(
        ["docker", "inspect", container, "--format", "{{json .Config.Env}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    settings = dict(item.split("=", 1) for item in json.loads(inspected.stdout))
    if (
        settings.get("AVAILABILITY_CACHE_ENABLED") != enabled
        or settings.get("AVAILABILITY_CACHE_STATS") != str(stats).lower()
    ):
        raise RunFailed(f"the API did not pick up {config} (stats={stats})")


def loadtest_command(*args: str) -> dict[str, Any]:
    output = compose("exec", "-T", "api", "python", "-m", "app.loadtest", *args)
    return json.loads(output)


@dataclass
class Sampler:
    """Samples container CPU and Postgres activity once a second in the background."""

    started: float = field(default_factory=time.time)
    cpu: list[dict[str, Any]] = field(default_factory=list)
    postgres: list[dict[str, Any]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _threads: list[threading.Thread] = field(default_factory=list)
    _stats: subprocess.Popen[str] | None = None

    def start(self) -> None:
        names = [compose("ps", "-q", service).strip() for service in ("api", "postgres")]
        self._names = dict(zip(names, ("api", "postgres"), strict=True))
        self._stats = subprocess.Popen(
            ["docker", "stats", "--format", "{{json .}}", *names],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for target in (self._read_cpu, self._poll_postgres):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._stats is not None:
            self._stats.terminate()
        for thread in self._threads:
            thread.join(timeout=5)
        return {"started": self.started, "cpu": self.cpu, "postgres": self.postgres}

    def _read_cpu(self) -> None:
        if self._stats is None or self._stats.stdout is None:
            return
        for line in self._stats.stdout:
            if self._stop.is_set():
                return
            # Each line is wrapped in terminal escape codes; keep the JSON object.
            start, end = line.find("{"), line.rfind("}")
            if start < 0 or end < start:
                continue
            try:
                row = json.loads(line[start : end + 1])
            except json.JSONDecodeError:
                continue
            service = self._names.get(row.get("Container", ""))
            if service is None:
                continue
            self.cpu.append(
                {
                    "t": time.time(),
                    "service": service,
                    "cpu_percent": float(row["CPUPerc"].rstrip("%")),
                }
            )

    def _poll_postgres(self) -> None:
        """One psql session for the whole run, so sampling costs one connection and no
        process start per sample. Four samples a second: most statements take a few
        milliseconds, so slower sampling mostly sees idle connections."""
        psql = subprocess.Popen(
            [
                *COMPOSE,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "gpureserve",
                "-d",
                "gpureserve",
                "-At",
                "-F",
                ",",
                "-q",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        if psql.stdin is None or psql.stdout is None:
            return
        try:
            while not self._stop.wait(PG_SAMPLE_INTERVAL):
                psql.stdin.write(PG_SAMPLE + "\n")
                psql.stdin.flush()
                line = psql.stdout.readline()
                if not line:
                    return
                active, in_transaction, total, commits = (int(v) for v in line.split(","))
                self.postgres.append(
                    {
                        "t": time.time(),
                        "active": active,
                        "in_transaction": in_transaction,
                        "connections": total,
                        "commits": commits,
                    }
                )
        finally:
            psql.terminate()


def run_k6(script: str, run_dir: Path, fixture: Path, args: argparse.Namespace) -> float:
    """Run one script; returns the time k6 was started."""
    env = {
        "FIXTURE": str(fixture),
        "BASE_URL": BASE_URL,
        "SUMMARY_OUT": str(run_dir / "summary.json"),
        "VUS": str(args.vus),
        "WARMUP": args.warmup,
        "MEASURE": args.measure,
    }
    command = ["k6", "run", "--quiet", "--no-color"]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command.append(str(HERE / f"{script}.js"))
    started = time.time()
    with (run_dir / "k6.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode == K6_ABORTED_BY_THRESHOLD:
        raise RunFailed(f"{script} aborted on a 401 or 429; see {run_dir / 'k6.log'}")
    if result.returncode != 0:
        raise RunFailed(f"{script} failed (exit {result.returncode}); see {run_dir / 'k6.log'}")
    return started


def one_run(script: str, config: str, repeat: int, out: Path, args: argparse.Namespace) -> None:
    run_dir = out / config / f"{script}-{repeat}"
    run_dir.mkdir(parents=True)
    fixture = run_dir / "fixture.json"
    data = loadtest_command("seed")
    fixture.write_text(json.dumps(data))
    sampler = Sampler()
    sampler.start()
    try:
        started = run_k6(script, run_dir, fixture, args)
    finally:
        samples = sampler.stop()
    samples["k6_started"] = started
    (run_dir / "samples.json").write_text(json.dumps(samples))
    meta = {"script": script, "config": config, "repeat": repeat, "counts": data["counts"]}
    if script == "booking_race":
        meta["verify"] = loadtest_command("verify")
        if not meta["verify"]["ok"]:
            (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
            raise RunFailed(f"race correctness check failed: {meta['verify']}")
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2))
    print(f"  {config} {script} #{repeat}: {(run_dir / 'k6.log').read_text().strip()}")


def hit_rate_run(script: str, out: Path, args: argparse.Namespace) -> None:
    """An untimed run with counting on: hits and misses during warm-up and after."""
    run_dir = out / "hit-rate" / script
    run_dir.mkdir(parents=True)
    fixture = run_dir / "fixture.json"
    fixture.write_text(json.dumps(loadtest_command("seed")))
    loadtest_command("cache-stats", "--reset")
    result: dict[str, Any] = {}

    def at_warmup_end() -> None:
        result["warmup"] = loadtest_command("cache-stats", "--reset")

    timer = threading.Timer(seconds(args.warmup), at_warmup_end)
    timer.start()
    try:
        run_k6(script, run_dir, fixture, args)
    finally:
        timer.cancel()
    result["measure"] = loadtest_command("cache-stats", "--reset")
    (run_dir / "hit_rate.json").write_text(json.dumps(result, indent=2))
    print(f"  hit rate {script}: {result}")


def machine() -> dict[str, Any]:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(
                ["sysctl", "-n", name], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    docker = subprocess.run(
        ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}} {{.OperatingSystem}}"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split(maxsplit=2)
    k6 = subprocess.run(["k6", "version"], capture_output=True, text=True, check=False)
    git = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    dirty = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True
    )
    return {
        "host": {
            "model": sysctl("hw.model"),
            "cpu": sysctl("machdep.cpu.brand_string"),
            "cores": sysctl("hw.ncpu"),
            "memory_bytes": sysctl("hw.memsize"),
            "os": platform.platform(),
        },
        "docker": {
            "cpus": docker[0] if docker else "",
            "memory_bytes": docker[1] if len(docker) > 1 else "",
            "os": docker[2].strip() if len(docker) > 2 else "",
        },
        "k6": k6.stdout.strip(),
        "commit": git.stdout.strip(),
        "dirty": bool(dirty.stdout.strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scripts", default=",".join(ALL_SCRIPTS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--vus", type=int, default=20)
    parser.add_argument("--warmup", default="30s")
    parser.add_argument("--measure", default="90s")
    parser.add_argument("--skip-hit-rate", action="store_true")
    args = parser.parse_args()
    for duration in (args.warmup, args.measure):
        seconds(duration)
    scripts = [s for s in args.scripts.split(",") if s]
    unknown = set(scripts) - set(ALL_SCRIPTS)
    if unknown:
        parser.error(f"unknown scripts: {', '.join(sorted(unknown))}")

    out = HERE / "out" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    print(f"building and starting the stack; results in {out}")
    compose("build", "api")
    meta = {
        "started": datetime.now(UTC).isoformat(),
        "args": vars(args),
        "machine": machine(),
        "limits": "postgres 1.0 CPU / 768 MiB, api 0.75 CPU / 512 MiB, redis 0.25 CPU / 128 MiB",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    try:
        for repeat in range(1, args.repeats + 1):
            for config in CONFIGS:
                configure(config)
                for script in scripts:
                    one_run(script, config, repeat, out, args)
        if not args.skip_hit_rate:
            configure("cache-on", stats=True)
            for script in (s for s in scripts if s in CACHED_SCRIPTS):
                hit_rate_run(script, out, args)
    except RunFailed as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        # Leave the stack in its normal configuration.
        with contextlib.suppress(RunFailed):
            configure("cache-on")
    print(f"done: python3 loadtest/report.py {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
