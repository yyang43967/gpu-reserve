"""Agent evaluation commands, from backend/:

    uv run python -m app.agent.evaluation validate        # replay every case's expectations
    uv run python -m app.agent.evaluation describe        # write evals/agent/cases.md
    uv run python -m app.agent.evaluation run --repeat 2  # run ASSISTANT_MODEL on every case

`validate` and `run` need a Postgres role that may create databases (DATABASE_URL).
`validate` calls no API: it replays each outcome's write calls with a scripted model.

`run` needs the model's API key or vLLM endpoint, and OPENAI_API_KEY for policy-search
queries not yet in evals/agent/query_embeddings.json.gz. It refuses a working tree with
uncommitted changes unless --allow-dirty, which the run records.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import AIMessage

from app.agent.evaluation.cases import Case, CaseFileError, Reserve, load_cases
from app.agent.evaluation.database import Databases, check_can_create_databases
from app.agent.evaluation.describe import render
from app.agent.evaluation.oracle import ORACLE_ANSWER, replay_cases, static_problems
from app.agent.evaluation.record import (
    CASES_MD_PATH,
    CASES_PATH,
    QUERY_EMBEDDINGS_PATH,
    RUNS_DIR,
    git_state,
    run_config,
    run_id,
    summary,
    write_run,
)
from app.agent.evaluation.runner import (
    WARM_UP_TIMEOUT_SECONDS,
    CaseRunner,
    Options,
    eval_settings,
    run_cases,
    warm_up,
)
from app.agent.tools import build_tools
from app.core.config import Settings, get_settings
from app.llm.registry import build_chat_model
from app.llm.scripted import ScriptedChatModel
from app.rag.embeddings import CachedEmbedder, build_embedder
from app.rag.evaluation import EMBEDDINGS_PATH
from app.seed import DEFAULT_ADMIN_EMAIL, PLAN, SERVERS, STUDENTS

EXIT_OK = 0
EXIT_ERROR = 1
# Expected arguments may name a server or reservation instead of giving its id.
ARG_ALIASES = {"server": "server_id", "reservation": "reservation_id"}


# --- validate ---


def dataset_problems(cases: Sequence[Case]) -> list[str]:
    """Names that do not exist in the seed data, and arguments no tool takes."""
    users = {email for _, email in STUDENTS} | {DEFAULT_ADMIN_EMAIL}
    hosts = {spec.hostname for spec in SERVERS}
    seeded = {booking.purpose for booking in PLAN}
    tools = {tool.name: set(tool.args) for tool in build_tools()}
    problems: list[str] = []
    for case in cases:
        hooks = [op for path in case.expect for card in path.cards for op in card.before_decision]
        ops = [*case.setup, *hooks]
        known = seeded | {op.purpose for op in ops if isinstance(op, Reserve)}

        def need(kind: str, value: object, pool: set[str], case: Case = case) -> None:
            if isinstance(value, str) and value not in pool:
                problems.append(f"{case.id}: unknown {kind} {value!r}")

        need("user", case.user, users)
        for op in ops:
            need("server", op.server, hosts)
            if isinstance(op, Reserve):
                need("user", op.user, users)
        for path in case.expect:
            for call in path.calls:
                if call.name not in tools:
                    problems.append(f"{case.id}: unknown tool {call.name}")
                    continue
                for key, value in call.args.items():
                    if ARG_ALIASES.get(key, key) not in tools[call.name]:
                        problems.append(f"{case.id}: {call.name} takes no {key!r}")
                    if key == "server":
                        need("server", value, hosts)
                    if key == "reservation":
                        need("reservation", value, known)
            for name in path.forbidden:
                if name not in tools:
                    problems.append(f"{case.id}: unknown forbidden tool {name}")
            for card in path.cards:
                need("server", card.server, hosts)
                need("reservation", card.reservation, known)
            for email in path.protected_users:
                need("user", email, users)
            for created in path.db.created:
                need("server", created.server, hosts)
                need("user", created.user, users)
            for changed in path.db.changed:
                need("reservation", changed.reservation, known)
    return problems


async def validate(cases_path: Path) -> int:
    """Static checks, then a replay of every outcome through the services."""
    try:
        cases = load_cases(cases_path)
    except CaseFileError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    settings = eval_settings(get_settings(), Options())
    problems = dataset_problems(cases)
    if not problems:
        problems = await replay(settings, cases)
    for problem in problems:
        print(problem, file=sys.stderr)
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category.value] = counts.get(case.category.value, 0) + 1
    judged = sum(1 for case in cases if case.judge is not None)
    outcomes = sum(len(case.expect) for case in cases)
    print(f"{len(cases)} cases ({outcomes} outcomes replayed), {judged} judged")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")
    return EXIT_ERROR if problems else EXIT_OK


async def replay(settings: Settings, cases: Sequence[Case]) -> list[str]:
    admin_url = str(settings.database_url)
    await check_can_create_databases(admin_url)
    recorded = CachedEmbedder.load(EMBEDDINGS_PATH)
    databases = Databases(admin_url, prefix=f"eval_{uuid.uuid4().hex[:8]}")
    try:
        await databases.build_template(recorded, settings.kb_dir, quota_zone=settings.quota_zone)
        placeholder = ScriptedChatModel(responder=lambda _: AIMessage(ORACLE_ANSWER))
        options = Options(backoff_seconds=(0.0,))
        runner = CaseRunner(databases, settings, placeholder, recorded, options)
        problems = [p for case in cases for p in static_problems(case, databases.kb_anchors)]
        return problems or await replay_cases(runner, cases)
    finally:
        await databases.drop_template()


def describe(cases_path: Path, out: Path) -> int:
    out.write_text(render(load_cases(cases_path)), encoding="utf-8")
    print(f"Wrote {out}")
    return EXIT_OK


# --- run ---


def _query_embedder(settings: Settings) -> CachedEmbedder:
    """Recorded vectors first; new policy-search queries go to the API and are kept."""
    recorded = CachedEmbedder.load(EMBEDDINGS_PATH)
    vectors = dict(recorded.vectors)
    if QUERY_EMBEDDINGS_PATH.exists():
        vectors.update(CachedEmbedder.load(QUERY_EMBEDDINGS_PATH).vectors)
    inner = build_embedder(settings)
    return CachedEmbedder(recorded.model, recorded.dimensions, inner, vectors)


async def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        print("The working tree has uncommitted changes; commit them or pass --allow-dirty.")
        return EXIT_ERROR
    cases = load_cases(args.cases)
    if args.only:
        wanted = set(args.only.split(","))
        cases = [case for case in cases if case.id in wanted]
    options = Options(
        request_timeout_seconds=args.request_timeout,
        run_timeout_seconds=args.run_timeout,
        concurrency=args.concurrency,
        budget_seconds=None if args.budget_minutes is None else args.budget_minutes * 60,
    )
    base = get_settings()
    settings = eval_settings(base, options)
    admin_url = str(settings.database_url)
    await check_can_create_databases(admin_url)
    model = build_chat_model(settings)
    warm_model = build_chat_model(
        settings.model_copy(update={"assistant_request_timeout_seconds": WARM_UP_TIMEOUT_SECONDS})
    )
    query_embedder = _query_embedder(settings)
    known_vectors = set(query_embedder.vectors)
    databases = Databases(admin_url, prefix=f"eval_{uuid.uuid4().hex[:8]}")
    try:
        await databases.build_template(
            CachedEmbedder.load(EMBEDDINGS_PATH), settings.kb_dir, quota_zone=settings.quota_zone
        )
        cold_start = await warm_up(warm_model)
        print(f"Warm-up call: {cold_start:.1f} s")
        runner = CaseRunner(databases, settings, model, query_embedder, options)
        for repeat in range(1, args.repeat + 1):
            started = datetime.now(UTC)
            results = await run_cases(runner, cases)
            finished = datetime.now(UTC)
            name = run_id(settings, started, repeat)
            reported = [
                str(call["model"]) for r in results for call in r.model_calls if call.get("model")
            ]
            config = run_config(
                settings,
                options,
                run=name,
                repeat=repeat,
                cases_path=args.cases,
                case_ids=[case.id for case in cases],
                commit=commit,
                dirty=dirty,
                cold_start_seconds=cold_start if repeat == 1 else None,
                started=started,
                finished=finished,
                reported_models=reported,
            )
            write_run(RUNS_DIR / name, config, results)
            totals = summary(results)
            print(
                f"{name}: {totals['passed']}/{totals['scored']} passed, "
                f"{totals['wrong_cards']} wrong cards, {totals['excluded']} left out"
            )
    finally:
        await databases.drop_template()
        new = set(query_embedder.vectors) - known_vectors
        if new:
            previous = (
                set(CachedEmbedder.load(QUERY_EMBEDDINGS_PATH).vectors)
                if QUERY_EMBEDDINGS_PATH.exists()
                else set()
            )
            query_embedder.save(QUERY_EMBEDDINGS_PATH, keep=previous | new)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.agent.evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate", help="Replay every case through the services.")
    check.add_argument("--cases", type=Path, default=CASES_PATH)
    table = commands.add_parser("describe", help="Write the dataset as a Markdown table.")
    table.add_argument("--cases", type=Path, default=CASES_PATH)
    table.add_argument("--out", type=Path, default=CASES_MD_PATH)
    go = commands.add_parser("run", help="Run ASSISTANT_MODEL on the dataset.")
    go.add_argument("--cases", type=Path, default=CASES_PATH)
    go.add_argument("--repeat", type=int, default=2)
    go.add_argument("--only", help="Comma-separated case ids.")
    go.add_argument("--concurrency", type=int, default=Options.concurrency)
    go.add_argument("--request-timeout", type=float, default=Options.request_timeout_seconds)
    go.add_argument("--run-timeout", type=float, default=Options.run_timeout_seconds)
    go.add_argument(
        "--budget-minutes",
        type=float,
        help="Trial runs only: stop after this long and mark unfinished cases aborted. "
        "It biases against slow configurations, so compared runs do not use it.",
    )
    go.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return asyncio.run(validate(args.cases))
    if args.command == "describe":
        return describe(args.cases, args.out)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
