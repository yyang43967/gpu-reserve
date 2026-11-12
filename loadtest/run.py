#!/usr/bin/env python3
"""Run the load tests against docker-compose.loadtest.yml and keep every result.

    python3 loadtest/run.py                          # everything, 3 repeats
    python3 loadtest/run.py --scripts availability --repeats 1 \
        --steps 3 --hold 4 --warmup 5s --measure 10s
    python3 loadtest/run.py --scripts slot_search --skip-hit-rate \
        --capacity-from loadtest/out/<earlier run>    # latency runs only
    python3 loadtest/run.py --scripts slot_search --capacity-only  # capacity runs only

Two kinds of timed run, both open-model (k6 starts requests at a set rate whatever the
server does):
1. Capacity: the offered rate rises in steps to a ceiling per script, for each
   configuration. It gives the peak throughput and the highest rate sustained within
   the latency target (see analysis.py).
2. Latency: a constant rate, the same for cache off and on, at 65% of the median
   uncached rate sustained with no failures in step 1, so the comparison is of service
   time, not queueing. `--capacity-from` takes that rate from an earlier run's capacity
   results instead of running step 1 again.
Then the booking race, and the hit-rate runs.

Within each repeat, the cache-off and cache-on configurations alternate, so slow drift
on the host (thermal, background work) affects both alike. Before every run the API is
restarted, the database is reset to the same seed, the cache is emptied, and a short
warm-up from concurrent clients opens the API's database connections (on endpoints that
don't touch the cache). During
each run, a sampler records the CPU of the API and Postgres containers, and Postgres's
connections and commits four times a second.

The cache hit rate is measured in separate runs with counting turned on, because
counting adds a Redis round trip to every read; those runs' latencies are not reported.

Results go to loadtest/out/<UTC time>[-label]/ (ignored by Git); `report.py`
summarizes them. Standard library only, so it runs with the system python3. Needs docker
and k6.

By default it drives docker-compose.loadtest.yml on this machine. On a twin of the
production instance (loadtest/instance/README.md), name the Compose files, project, and
env file, point --base-url at the twin, and set DOCKER_HOST to reach its Docker:

    DOCKER_HOST=ssh://ec2-user@<twin> python3 loadtest/run.py --no-build \
        --compose-file infra/deploy/compose.yml --compose-file loadtest/compose.instance.yml \
        --project gpu-reserve-loadtest --env-file /opt/gpu-reserve-loadtest/.env \
        --base-url http://<twin>:80 --label burst

`--soak 30m --soak-rates ... --settle 10m` instead runs the memory soak (soak.js) and
nothing else.

Container memory is sampled with CPU, so a run's samples show how the API and worker
grew under load.
"""
# A host script that drives docker and k6 found on PATH, with fixed arguments.
# ruff: noqa: S603, S607

import argparse
import contextlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from analysis import (
    CONFIGS,
    LATENCY_LOAD_SHARE,
    RAMP_MAX,
    REQUESTS_PER_ITERATION,
    TIMED_SCRIPTS,
    capacity,
    median,
    memory_mib,
    memory_trend,
    seconds,
    steps,
)

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "loadtest"
# Set from the command line in main(); these are the local stack's.
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.loadtest.yml")]
BASE_URL = "http://127.0.0.1:18100"
LOCAL_LIMITS = "postgres 1.0 CPU / 768 MiB, api 0.75 CPU / 512 MiB, redis 0.25 CPU / 128 MiB"
# Containers whose CPU and memory are sampled, when the stack has them (the local
# stack has no worker or Caddy).
SAMPLED_SERVICES = ("api", "postgres", "worker", "caddy", "redis")
ALL_SCRIPTS = (*TIMED_SCRIPTS, "booking_race")
# Scripts that read through the cache, and so get a hit-rate run.
CACHED_SCRIPTS = ("availability", "slot_search")
K6_ABORTED_BY_THRESHOLD = 99
API_READY_TIMEOUT = 60
# Warm-up after each restart and seed: this many concurrent clients, each making
# WARM_UP_ROUNDS requests, so the pool opens its connections before k6 starts.
WARM_UP_CLIENTS = 10
WARM_UP_ROUNDS = 3
# Endpoints that use the database but not the availability cache, so warming up
# leaves the cache empty for the run.
WARM_UP_PATHS = ("/api/v1/me", "/api/v1/servers?page_size=20")

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


def fresh_api() -> None:
    """Restart the API and wait until it answers. Requests still queued from the
    previous run (k6 stops sending, but the server keeps working through what it
    accepted) would otherwise hold the connection pool at the start of the next run."""
    compose("restart", "api")
    deadline = time.time() + API_READY_TIMEOUT
    while time.time() < deadline:
        try:
            ready = f"{BASE_URL}/api/v1/ready"
            with urllib.request.urlopen(ready, timeout=2) as response:  # noqa: S310 - fixed http URL
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RunFailed("the API did not become ready after a restart")


def warm_up(fixture: dict[str, Any]) -> None:
    """A few authenticated requests from concurrent clients after a restart. A fresh
    process opens its pooled connections and loads code paths on first use; without
    this, that cost lands in the first seconds of the measured run."""
    tokens = fixture["students"][:WARM_UP_CLIENTS]

    def client(token: str) -> list[int]:
        codes = []
        for round_ in range(WARM_UP_ROUNDS):
            path = WARM_UP_PATHS[round_ % len(WARM_UP_PATHS)]
            request = urllib.request.Request(  # noqa: S310 - fixed http URL
                f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {token}"}
            )
            try:
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed http URL
                    codes.append(response.status)
            except urllib.error.HTTPError as exc:
                codes.append(exc.code)
        return codes

    with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
        codes = [code for result in pool.map(client, tokens) for code in result]
    if any(code != 200 for code in codes):
        raise RunFailed(f"warm-up requests failed: {sorted(set(codes))}")


def loadtest_command(*args: str) -> dict[str, Any]:
    output = compose("exec", "-T", "api", "python", "-m", "app.loadtest", *args)
    return json.loads(output)


@dataclass
class Sampler:
    """Samples container CPU and memory about once a second, and Postgres activity four
    times a second, in the background."""

    started: float = field(default_factory=time.time)
    cpu: list[dict[str, Any]] = field(default_factory=list)
    memory: list[dict[str, Any]] = field(default_factory=list)
    postgres: list[dict[str, Any]] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _threads: list[threading.Thread] = field(default_factory=list)
    _stats: subprocess.Popen[str] | None = None

    def start(self) -> None:
        running = compose("ps", "--services").split()
        services = [service for service in SAMPLED_SERVICES if service in running]
        names = [compose("ps", "-q", service).strip() for service in services]
        self._names = dict(zip(names, services, strict=True))
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
        return {
            "started": self.started,
            "cpu": self.cpu,
            "memory": self.memory,
            "postgres": self.postgres,
        }

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
            now = time.time()
            self.cpu.append(
                {"t": now, "service": service, "cpu_percent": float(row["CPUPerc"].rstrip("%"))}
            )
            with contextlib.suppress(ValueError):
                self.memory.append(
                    {"t": now, "service": service, "mem_mib": memory_mib(row["MemUsage"])}
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


def run_k6(
    script: str, run_dir: Path, fixture: Path, args: argparse.Namespace, mode: dict[str, str]
) -> float:
    """Run one script; returns the time k6 was started. `mode` holds MODE and its rate."""
    env = {
        "FIXTURE": str(fixture),
        "BASE_URL": BASE_URL,
        "SUMMARY_OUT": str(run_dir / "summary.json"),
        "WARMUP": args.warmup,
        "MEASURE": args.measure,
        "STEPS": str(args.steps),
        "HOLD": str(args.hold),
        **mode,
    }
    command = ["k6", "run", "--quiet", "--no-color"]
    if script == "soak":
        env |= {"SOAK": args.soak, "SOAK_RATES": format_rates(args.soak_rates)}
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


def one_run(
    script: str,
    config: str,
    repeat: int,
    out: Path,
    args: argparse.Namespace,
    mode: dict[str, str],
) -> dict[str, Any]:
    kind = mode.get("MODE", "race")
    run_dir = out / config / kind / f"{script}-{repeat}"
    run_dir.mkdir(parents=True)
    fresh_api()
    fixture = run_dir / "fixture.json"
    data = loadtest_command("seed")
    fixture.write_text(json.dumps(data))
    warm_up(data)
    sampler = Sampler()
    sampler.start()
    try:
        started = run_k6(script, run_dir, fixture, args, mode)
    finally:
        samples = sampler.stop()
    samples["k6_started"] = started
    (run_dir / "samples.json").write_text(json.dumps(samples))
    meta = {
        "script": script,
        "config": config,
        "repeat": repeat,
        "kind": kind,
        "mode": mode,
        "counts": data["counts"],
    }
    if script == "booking_race":
        meta["verify"] = loadtest_command("verify")
        if not meta["verify"]["ok"]:
            (run_dir / "run.json").write_text(json.dumps(meta, indent=2, default=str))
            raise RunFailed(f"race correctness check failed: {meta['verify']}")
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2, default=str))
    log = (run_dir / "k6.log").read_text().strip().replace("\n", "\n    ")
    print(f"  {config} {kind} {script} #{repeat}:\n    {log}", flush=True)
    return json.loads((run_dir / "summary.json").read_text())


def hit_rate_run(script: str, out: Path, args: argparse.Namespace, mode: dict[str, str]) -> None:
    """An untimed run with counting on: hits and misses during warm-up and after."""
    run_dir = out / "hit-rate" / script
    run_dir.mkdir(parents=True)
    fresh_api()
    fixture = run_dir / "fixture.json"
    data = loadtest_command("seed")
    fixture.write_text(json.dumps(data))
    warm_up(data)
    loadtest_command("cache-stats", "--reset")
    result: dict[str, Any] = {}

    def at_warmup_end() -> None:
        result["warmup"] = loadtest_command("cache-stats", "--reset")

    timer = threading.Timer(seconds(args.warmup), at_warmup_end)
    timer.start()
    try:
        run_k6(script, run_dir, fixture, args, mode)
    finally:
        timer.cancel()
    result["measure"] = loadtest_command("cache-stats", "--reset")
    (run_dir / "hit_rate.json").write_text(json.dumps(result, indent=2))
    print(f"  hit rate {script}: {result}")


def soak_run(out: Path, args: argparse.Namespace) -> None:
    """The memory soak: soak.js at fixed rates for --soak, then --settle with no load,
    sampling all along. The API is restarted once, before the load, and not again."""
    run_dir = out / "soak"
    run_dir.mkdir(parents=True)
    configure("cache-on")
    fresh_api()
    fixture = run_dir / "fixture.json"
    data = loadtest_command("seed")
    fixture.write_text(json.dumps(data))
    warm_up(data)
    sampler = Sampler()
    sampler.start()
    try:
        started = run_k6("soak", run_dir, fixture, args, {})
        print(f"  soak load done; settling for {args.settle}", flush=True)
        time.sleep(seconds(args.settle))
    finally:
        samples = sampler.stop()
    samples["k6_started"] = started
    (run_dir / "samples.json").write_text(json.dumps(samples))
    load_seconds = seconds(args.soak)
    trends = {
        service: memory_trend(samples["memory"], service, started, load_seconds)
        for service in sorted({s["service"] for s in samples["memory"]})
    }
    meta = {
        "kind": "soak",
        "soak": args.soak,
        "settle": args.settle,
        "rates": args.soak_rates,
        "counts": data["counts"],
        "memory": trends,
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2, default=str))
    log = (run_dir / "k6.log").read_text().strip().replace("\n", "\n    ")
    print(f"  soak:\n    {log}", flush=True)
    for service, trend in trends.items():
        print(f"  memory {service}: " + ", ".join(f"{k} {v:.1f}" for k, v in trend.items()))


def target() -> dict[str, Any]:
    """What the run was pointed at: Compose command, base URL, Docker host, and the
    API's and Caddy's effective settings (uvicorn flags, upstream keep-alive)."""
    found: dict[str, Any] = {
        "compose": COMPOSE,
        "base_url": BASE_URL,
        "docker_host": os.environ.get("DOCKER_HOST", ""),
    }
    running = compose("ps", "--services").split()
    for service in (s for s in ("api", "caddy") if s in running):
        container = compose("ps", "-q", service).strip()
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                container,
                "--format",
                "{{json .Config.Cmd}}|{{json .HostConfig.CgroupParent}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        command, cgroup = inspected.split("|", 1)
        found[service] = {"command": json.loads(command), "cgroup_parent": json.loads(cgroup)}
    for name in ("API_KEEP_ALIVE_SECONDS", "API_UPSTREAM_KEEPALIVE"):
        found[name] = os.environ.get(name, "")
    return found


COMMIT_UNKNOWN = "commit unknown"


def source_commit() -> tuple[str, bool | None]:
    """The commit being run, and whether the tree had changes. A source archive (as on
    the load-test driver) has no Git metadata; upload-source.sh stamps its commit in a
    COMMIT file and only archives a clean tree."""
    stamped = ROOT / "COMMIT"
    if stamped.is_file():
        return stamped.read_text().strip(), False
    try:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        status = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "", None
    return head.stdout.strip(), bool(status.stdout.strip())


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
    commit, dirty = source_commit()
    if not commit:
        print(
            "warning: the commit under test is unknown (no COMMIT file and no Git checkout);"
            f" this run's results are marked {COMMIT_UNKNOWN!r}",
            file=sys.stderr,
            flush=True,
        )
        commit = COMMIT_UNKNOWN
    return {
        "host": {
            "model": sysctl("hw.model"),
            "cpu": sysctl("machdep.cpu.brand_string"),
            "cores": sysctl("hw.ncpu") or str(os.cpu_count() or ""),
            "memory_bytes": sysctl("hw.memsize"),
            "os": platform.platform(),
        },
        "docker": {
            "cpus": docker[0] if docker else "",
            "memory_bytes": docker[1] if len(docker) > 1 else "",
            "os": docker[2].strip() if len(docker) > 2 else "",
        },
        "k6": k6.stdout.strip(),
        "commit": commit,
        "dirty": dirty,
    }


def ramp_max(script: str, args: argparse.Namespace) -> float:
    """The capacity ramp's last step for a script: --ramp-max, or the default."""
    return args.ramp_max.get(script, RAMP_MAX[script])


def clean_sustained(summary: dict[str, Any], script: str, args: argparse.Namespace) -> float:
    rows = steps(
        summary,
        script=script,
        ramp_max=ramp_max(script, args),
        step_count=args.steps,
        hold=args.hold,
    )
    return capacity(rows)["clean_sustained_rps"]


def earlier_capacity(
    run: Path, scripts: list[str], args: argparse.Namespace
) -> dict[str, list[float]]:
    """Clean sustained rates from an earlier run's uncached capacity results."""
    found: dict[str, list[float]] = {}
    for script in scripts:
        summaries = sorted((run / "cache-off" / "capacity").glob(f"{script}-*/summary.json"))
        if not summaries:
            raise RunFailed(f"no uncached capacity results for {script} in {run}")
        found[script] = [
            clean_sustained(json.loads(path.read_text()), script, args) for path in summaries
        ]
    return found


def latency_rates(
    sustained: dict[str, list[float]], overrides: dict[str, float]
) -> dict[str, float]:
    """Iterations per second for the latency runs: a share of the median uncached rate
    sustained with no failures."""
    rates = {}
    for script, values in sustained.items():
        per_second = median(values) / REQUESTS_PER_ITERATION[script]
        rates[script] = round(LATENCY_LOAD_SHARE * per_second, 1)
    return rates | overrides


def format_rates(rates: dict[str, float]) -> str:
    return ",".join(f"{name}={value}" for name, value in rates.items())


def parse_rates(text: str) -> dict[str, float]:
    rates = {}
    for item in filter(None, text.split(",")):
        name, _, value = item.partition("=")
        if name not in TIMED_SCRIPTS:
            raise argparse.ArgumentTypeError(f"unknown script {name}")
        rates[name] = float(value)
    return rates


def main() -> int:
    # The target is chosen on the command line; every helper uses these.
    global COMPOSE, BASE_URL
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scripts", default=",".join(ALL_SCRIPTS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--steps", type=int, default=10, help="capacity steps")
    parser.add_argument("--hold", type=int, default=12, help="seconds per capacity step")
    parser.add_argument("--warmup", default="30s", help="latency warm-up")
    parser.add_argument("--measure", default="90s", help="latency measured phase")
    parser.add_argument(
        "--rates",
        type=parse_rates,
        default={},
        help="fixed latency rates (iterations/s), e.g. availability=110; skips capacity "
        "runs for the scripts named",
    )
    parser.add_argument(
        "--capacity-from",
        type=Path,
        help="an earlier run directory whose uncached capacity results set the latency "
        "rates; skips the capacity runs",
    )
    parser.add_argument("--skip-hit-rate", action="store_true")
    parser.add_argument(
        "--capacity-only",
        action="store_true",
        help="stop after the capacity runs: no latency runs, race, or hit-rate runs",
    )
    parser.add_argument(
        "--compose-file",
        action="append",
        type=Path,
        help="Compose file to drive instead of docker-compose.loadtest.yml (repeatable)",
    )
    parser.add_argument("--project", help="Compose project name, with --compose-file")
    parser.add_argument("--env-file", type=Path, help="Compose env file, with --compose-file")
    parser.add_argument("--base-url", default=BASE_URL, help="API origin k6 sends to")
    parser.add_argument("--no-build", action="store_true", help="use the image as it is")
    parser.add_argument("--label", default="", help="appended to the results directory")
    parser.add_argument("--soak", help="run only the memory soak for this long, e.g. 30m")
    parser.add_argument(
        "--soak-rates",
        type=parse_rates,
        default={},
        help="soak iterations/s per script, e.g. browse_servers=10,availability=40,slot_search=4",
    )
    parser.add_argument("--settle", default="10m", help="sampling after the soak's load")
    parser.add_argument(
        "--ramp-max",
        type=parse_rates,
        default={},
        help="capacity ramp ceilings (iterations/s) instead of the defaults, "
        "e.g. availability=200,slot_search=45",
    )
    parser.add_argument(
        "--configs",
        default=",".join(CONFIGS),
        help="cache configurations to run, e.g. cache-on (default: both)",
    )
    args = parser.parse_args()
    for duration in (args.warmup, args.measure, args.settle, *([args.soak] if args.soak else [])):
        seconds(duration)
    if args.soak and set(args.soak_rates) != set(TIMED_SCRIPTS):
        parser.error(f"--soak needs --soak-rates for {', '.join(TIMED_SCRIPTS)}")
    configs = [c for c in args.configs.split(",") if c]
    if not configs or set(configs) - set(CONFIGS):
        parser.error(f"--configs takes {', '.join(CONFIGS)}")
    if (args.project or args.env_file) and not args.compose_file:
        parser.error("--project and --env-file go with --compose-file")
    if args.compose_file:
        COMPOSE = ["docker", "compose"]
        if args.project:
            COMPOSE += ["-p", args.project]
        for path in args.compose_file:
            COMPOSE += ["-f", str(path.resolve())]
        if args.env_file:
            COMPOSE += ["--env-file", str(args.env_file)]
    BASE_URL = args.base_url.rstrip("/")
    scripts = [s for s in args.scripts.split(",") if s]
    unknown = set(scripts) - set(ALL_SCRIPTS)
    if unknown:
        parser.error(f"unknown scripts: {', '.join(sorted(unknown))}")
    if args.capacity_only and args.capacity_from:
        parser.error("--capacity-only runs capacity; --capacity-from skips it")
    timed = [s for s in scripts if s in TIMED_SCRIPTS]
    to_ramp = [] if args.capacity_from else [s for s in timed if s not in args.rates]

    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + (f"-{args.label}" if args.label else "")
    out = HERE / "out" / name
    out.mkdir(parents=True)
    print(f"starting the stack; results in {out}", flush=True)
    if not args.no_build:
        compose("build", "api")
    meta: dict[str, Any] = {
        "started": datetime.now(UTC).isoformat(),
        "args": vars(args),
        "ramp_max": {s: ramp_max(s, args) for s in to_ramp},
        "capacity_from": str(args.capacity_from) if args.capacity_from else None,
        "machine": machine(),
        "limits": LOCAL_LIMITS if not args.compose_file else "as in the Compose files",
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    try:
        configure("cache-on")
        meta["target"] = target()
        (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        if args.soak:
            soak_run(out, args)
            print(f"done: {out}")
            return 0
        sustained: dict[str, list[float]] = {s: [] for s in to_ramp}
        if args.capacity_from:
            fixed = [s for s in timed if s not in args.rates]
            sustained = earlier_capacity(args.capacity_from, fixed, args)
            meta["clean_sustained"] = sustained
        for repeat in range(1, args.repeats + 1):
            for config in configs:
                if not to_ramp:
                    break
                configure(config)
                for script in to_ramp:
                    mode = {"MODE": "capacity", "RAMP_MAX": str(ramp_max(script, args))}
                    summary = one_run(script, config, repeat, out, args, mode)
                    # The latency rate comes from the uncached runs, or the cached ones
                    # when only those run.
                    if config == configs[0]:
                        sustained[script].append(clean_sustained(summary, script, args))

        rates = latency_rates(sustained, args.rates)
        # No step was sustained without errors, so there is no rate below capacity to
        # compare at. The capacity results stand; the latency runs are skipped.
        unrated = sorted(s for s in timed if rates.get(s, 0) <= 0)
        for script in unrated:
            print(
                f"warning: {script} sustained no step without errors; skipping its latency"
                " runs (lower --ramp-max to resolve the knee)",
                file=sys.stderr,
                flush=True,
            )
        meta["latency_skipped"] = unrated
        timed = [s for s in timed if s not in unrated]
        meta["latency_rates"] = rates
        (out / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
        print(f"latency rates (iterations/s): {rates}", flush=True)
        if args.capacity_only:
            print(f"done: python3 loadtest/report.py {out}")
            return 0

        for repeat in range(1, args.repeats + 1):
            for config in configs:
                configure(config)
                for script in timed:
                    mode = {"MODE": "latency", "RATE": str(rates[script])}
                    one_run(script, config, repeat, out, args, mode)
                if "booking_race" in scripts:
                    one_run("booking_race", config, repeat, out, args, {})
        if not args.skip_hit_rate:
            configure("cache-on", stats=True)
            for script in (s for s in timed if s in CACHED_SCRIPTS):
                hit_rate_run(script, out, args, {"MODE": "latency", "RATE": str(rates[script])})
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
