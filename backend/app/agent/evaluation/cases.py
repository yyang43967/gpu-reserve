"""Evaluation cases: what the user says, what the database holds first, and what must be
true afterwards.

Cases name things the way a person would: users by email, servers by hostname, and
existing reservations by their purpose. The runner resolves those names in each case's
own database, because ids are generated afresh for every copy of the seed data.

Every time in a case is absolute (ISO 8601 with an offset). All cases run at EVAL_NOW,
so "tomorrow" always means the same day.
"""

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

# Tuesday, 10:00 in New York, 09:00 in Chicago, 07:00 in Los Angeles: inside the peak
# window in two labs, in the middle of a quota week, with the 14-day horizon ending on
# November 24.
EVAL_NOW: Final = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


class Category(StrEnum):
    BOOKING = "booking"
    RULE_VIOLATION = "rule_violation"
    CONFLICT = "conflict"
    CANCEL = "cancel"
    CLARIFY = "clarify"
    SLOTS = "slots"
    POLICY = "policy"
    HARDWARE = "hardware"
    OUT_OF_SCOPE = "out_of_scope"
    OTHER_USERS = "other_users"
    INJECTION = "injection"
    MULTI_TURN = "multi_turn"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- setup: overlays and hooks ---


class Reserve(_Strict):
    """A reservation made through the booking service, as if `user` had booked it a day
    before it starts. `approve` has an admin approve a pending one."""

    op: Literal["reserve"] = "reserve"
    user: str
    server: str
    gpu_count: int = Field(ge=1)
    start: AwareDatetime
    end: AwareDatetime
    purpose: str
    approve: bool = False


class SetServerNotes(_Strict):
    op: Literal["server_notes"] = "server_notes"
    server: str
    notes: str


Setup = Annotated[Reserve | SetServerNotes, Field(discriminator="op")]


# --- turns ---


class Decision(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    # Leave the card open; the next turn's message supersedes it, as when a user types
    # "actually, make it four GPUs" instead of clicking.
    LEAVE = "leave"


class Turn(_Strict):
    say: str = Field(min_length=1)


# --- expectations ---


class ExpectedCall(_Strict):
    """A tool call that must appear at least once. Each argument is either a value
    (times compare as instants, text without regard to case) or one operator:
    {"contains": text}, {"at_most": value}, {"at_least": value}, {"one_of": [values]}.
    `server` stands for `server_id`, given as a hostname, and `reservation` for
    `reservation_id`, given as the reservation's purpose. `turn` is the turn the call
    belongs to; validation replays the calls turn by turn."""

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    turn: int = Field(default=0, ge=0)


class ExpectedCard(_Strict):
    """A confirmation card, and how the simulated user answers it. Only the fields
    given are compared with the card shown."""

    action: Literal["create_reservation", "cancel_reservation"]
    decision: Decision
    # Run just before the decision, e.g. someone else taking the GPUs between the card
    # and the click.
    before_decision: list[Setup] = Field(default_factory=list)
    server: str | None = None
    gpus: int | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    status_after: str | None = None
    effect: Literal["cancel", "end_early"] | None = None
    new_end: AwareDatetime | None = None
    # The purpose of the reservation being cancelled, for cancel cards.
    reservation: str | None = None
    purpose_contains: str | None = None


class CreatedRow(_Strict):
    user: str | None = None
    server: str
    gpu_count: int
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    status: str
    purpose_contains: str | None = None


class ChangedRow(_Strict):
    # An existing reservation, named by its purpose.
    reservation: str
    status: str
    end: AwareDatetime | None = None


class ExpectedDb(_Strict):
    """The complete change to reservations: anything else that changed fails the case."""

    created: list[CreatedRow] = Field(default_factory=list)
    changed: list[ChangedRow] = Field(default_factory=list)


class Expectation(_Strict):
    calls: list[ExpectedCall] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    # Exactly these cards, in order. An empty list means no card at all.
    cards: list[ExpectedCard] = Field(default_factory=list)
    # Error types that must appear in tool results.
    errors: list[str] = Field(default_factory=list)
    db: ExpectedDb = Field(default_factory=ExpectedDb)
    # Outcomes in assistant_actions, in order (confirmed, cancelled, superseded).
    actions: list[Literal["confirmed", "cancelled", "superseded"]] | None = None
    # At least one cited source must be from one of these, as "document" or
    # "document#heading".
    citations: list[str] = Field(default_factory=list)
    # The final answer must ask a question that mentions one of these terms.
    question_terms: list[str] = Field(default_factory=list)
    # The final answer must mention at least one of these, e.g. the rule that refused
    # a booking.
    mentions: list[str] = Field(default_factory=list)
    # Text that must not appear in any answer: other users' data, injected payloads.
    must_not_mention: list[str] = Field(default_factory=list)


class JudgeSpec(_Strict):
    criteria: list[Literal["grounded", "answers", "declines"]] = Field(min_length=1)
    # The facts a correct answer conveys, for the "answers" criterion.
    reference: list[str] = Field(default_factory=list)


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    category: Category
    user: str
    # The expected outcome in plain words, for people reviewing the dataset.
    outcome: str = Field(min_length=1)
    note: str = ""
    setup: list[Setup] = Field(default_factory=list)
    turns: list[Turn] = Field(min_length=1)
    # Acceptable outcomes; the case passes if any one of them holds.
    expect: list[Expectation] = Field(min_length=1)
    judge: JudgeSpec | None = None

    @model_validator(mode="before")
    @classmethod
    def _one_expectation_or_many(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("expect"), dict):
            return {**data, "expect": [data["expect"]]}
        return data

    @model_validator(mode="after")
    def _one_plan_for_every_card(self) -> "Case":
        """Outcomes may expect different cards, but wherever two of them expect a card
        at the same position, they must answer it the same way: the runner has to
        decide before it knows which outcome the model is heading for."""
        for index in range(max(len(path.cards) for path in self.expect)):
            plans = {
                (card.decision, tuple(card.before_decision))
                for path in self.expect
                if index < len(path.cards)
                for card in [path.cards[index]]
            }
            if len(plans) > 1:
                raise ValueError(f"outcomes answer card {index + 1} differently")
        for path in self.expect:
            for call in path.calls:
                if call.turn >= len(self.turns):
                    raise ValueError(f"{call.name} is placed in turn {call.turn}, which is missing")
        return self

    def card_plan(self) -> list[tuple[Decision, list[Setup]]]:
        """How the simulated user answers the n-th card shown. A card past the end of
        the plan was not expected by any outcome."""
        plan: list[tuple[Decision, list[Setup]]] = []
        longest = max(self.expect, key=lambda path: len(path.cards))
        for card in longest.cards:
            plan.append((card.decision, list(card.before_decision)))
        return plan


class CaseFileError(ValueError):
    pass


def load_cases(path: Path) -> list[Case]:
    cases: list[Case] = []
    errors: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(Case.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(f"line {number}: {exc}")
    ids = [case.id for case in cases]
    errors.extend(f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1}))
    if errors:
        raise CaseFileError("\n".join(errors))
    return cases


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
