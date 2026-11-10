"""Validating the dataset against the real services.

For every acceptable outcome of every case, a scripted model makes that outcome's
expected write calls (`propose_reservation`, `cancel_my_reservation`) turn by turn,
through the real tools, in the case's own database copy, with the case's planned card
decisions. The outcome then has to hold on everything the services decide: the cards,
the expected error types, the exact reservation diff, and the action outcomes. A case
whose expectations the services contradict, for example a "peak violation" that the
rules actually accept, fails validation and does not ship.

Read calls and text checks are not replayed: they depend on the model, not on the
rules, and are checked when a real model runs.
"""

import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.evaluation.cases import EVAL_NOW, Case, Expectation, ExpectedCall
from app.agent.evaluation.runner import CaseResult, CaseRunner, run_cases
from app.agent.evaluation.scoring import Names
from app.agent.tools import WRITE_GATED_TOOLS
from app.llm.scripted import ScriptedChatModel
from app.models import GpuModel
from app.services import booking_rules

# What the replay's model says when it has made its calls for a turn.
ORACLE_ANSWER: Final = "Done."
# A slot search may start from the current half hour, a little before the fixed clock.
SLOT_GRACE: Final = timedelta(minutes=30)
# Checks decided by the services rather than by the model's wording.
SERVICE_CHECKS: Final = ("completed", "cards", "db", "actions")


def representative(value: Any) -> Any:
    """A concrete value that satisfies an expected argument, for replaying it."""
    if isinstance(value, dict) and len(value) == 1:
        ((op, operand),) = value.items()
        if op in ("contains", "at_most", "at_least"):
            return operand
        if op == "one_of":
            return operand[0]
    return value


def _created_ids(messages: Sequence[BaseMessage]) -> dict[str, str]:
    """Purpose -> id of reservations booked earlier in this conversation."""
    found: dict[str, str] = {}
    for message in messages:
        if isinstance(message, ToolMessage):
            try:
                result = json.loads(str(message.content))
            except json.JSONDecodeError:
                continue
            reservation = result.get("reservation") if isinstance(result, dict) else None
            if isinstance(reservation, dict) and "purpose" in reservation:
                found[str(reservation["purpose"])] = str(reservation["id"])
    return found


def _arguments(call: ExpectedCall, names: Names, messages: Sequence[BaseMessage]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for key, value in call.args.items():
        concrete = representative(value)
        if key == "server":
            args["server_id"] = str(names.servers.get(concrete, concrete))
        elif key == "reservation":
            known = {p: str(i) for p, i in names.reservations.items()}
            known.update(_created_ids(messages))
            args["reservation_id"] = known.get(concrete, concrete)
        else:
            args[key] = concrete
    return args


def replay_responder(path: Expectation, names: Names) -> Callable[[list[BaseMessage]], AIMessage]:
    writes = [call for call in path.calls if call.name in WRITE_GATED_TOOLS]

    def respond(messages: list[BaseMessage]) -> AIMessage:
        humans = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
        turn = len(humans) - 1
        made = sum(1 for m in messages[humans[-1] :] if isinstance(m, AIMessage) and m.tool_calls)
        due = [call for call in writes if call.turn == turn]
        if made >= len(due):
            return AIMessage(ORACLE_ANSWER)
        call = due[made]
        return AIMessage(
            "",
            tool_calls=[
                {
                    "name": call.name,
                    "args": _arguments(call, names, messages),
                    "id": f"call_{turn}_{made}",
                }
            ],
        )

    return respond


def static_problems(case: Case, anchors: set[tuple[str, str]]) -> list[str]:
    """Rules about an outcome that need no replay: errors need a write call that could
    raise them, every cited section exists in the ingested knowledge base, and expected
    slot searches fit the fixed clock."""
    problems: list[str] = []
    documents = {document for document, _ in anchors}
    for index, path in enumerate(case.expect, start=1):
        where = f"{case.id} outcome {index}"
        writes = [call for call in path.calls if call.name in WRITE_GATED_TOOLS]
        if path.errors and not writes:
            problems.append(
                f"{where}: expects errors {path.errors} but makes no write call that "
                "could raise them"
            )
        for citation in path.citations:
            document, _, heading = citation.partition("#")
            if document not in documents or (heading and (document, heading) not in anchors):
                problems.append(f"{where}: no section {citation!r} in kb_chunks")
        for call in path.calls:
            if call.name == "find_slots":
                problems.extend(f"{where}: find_slots {p}" for p in slot_problems(call))
    return problems


def slot_problems(call: ExpectedCall) -> list[str]:
    """Expected find_slots arguments that no correct search at EVAL_NOW could use."""
    args = {key: representative(value) for key, value in call.args.items()}
    problems: list[str] = []
    times: dict[str, datetime] = {}
    for key in ("earliest", "latest"):
        if key in args:
            moment = datetime.fromisoformat(str(args[key]))
            times[key] = moment
            if moment.tzinfo is None:
                problems.append(f"{key} has no offset")
            elif moment < EVAL_NOW - SLOT_GRACE:
                problems.append(f"{key} {moment.isoformat()} is before the fixed clock")
            elif moment > EVAL_NOW + booking_rules.BOOKING_HORIZON + SLOT_GRACE:
                problems.append(f"{key} {moment.isoformat()} is past the booking horizon")
    if "earliest" in times and "latest" in times and times["earliest"] >= times["latest"]:
        problems.append("earliest is not before latest")
    duration = args.get("duration_minutes")
    if duration is not None and (int(duration) < 30 or int(duration) % 30):
        problems.append(f"duration_minutes {duration} is not a positive multiple of 30")
    if "gpu_model" in args and args["gpu_model"] not in {m.value for m in GpuModel}:
        problems.append(f"unknown gpu_model {args['gpu_model']!r}")
    return problems


def replay_problems(result: CaseResult, case: Case, index: int) -> list[str]:
    if result.excluded:
        return [f"{case.id} outcome {index}: replay did not run ({result.status})"]
    failed = [
        check
        for check in result.checks
        if not check["passed"]
        and (
            check["name"] in SERVICE_CHECKS
            or check["name"].startswith("error:")
            or check["name"].removeprefix("call:") in WRITE_GATED_TOOLS
        )
    ]
    return [f"{case.id} outcome {index}: {c['name']} - {c['detail']}" for c in failed]


async def replay_cases(runner: CaseRunner, cases: Sequence[Case]) -> list[str]:
    """Replays every outcome of every case and returns what the services contradicted."""
    single: list[tuple[Case, Case, int]] = []
    for case in cases:
        for index, path in enumerate(case.expect, start=1):
            only = case.model_copy(update={"id": f"{case.id}--{index}", "expect": [path]})
            single.append((only, case, index))
    paths = {only.id: only.expect[0] for only, _, _ in single}
    runner.model_for = lambda c, names: ScriptedChatModel(
        responder=replay_responder(paths[c.id], names)
    )
    results = await run_cases(runner, [only for only, _, _ in single])
    problems: list[str] = []
    for (_, case, index), result in zip(single, results, strict=True):
        problems.extend(replay_problems(result, case, index))
    return problems
