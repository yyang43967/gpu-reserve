"""The knowledge base states the rules the code enforces.

Expected phrases are built from the same constants the services use, so changing a rule
without updating the policy documents fails here.
"""

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.exceptions import DomainError
from app.core.security import PASSWORD_MIN_LENGTH
from app.models.reservation import PURPOSE_MAX_LENGTH
from app.models.server import DEFAULT_MAX_BOOKING_HOURS, GPU_MEMORY_GB, GpuModel
from app.models.user import DEFAULT_WEEKLY_GPU_HOUR_QUOTA
from app.schemas.auth import PASSWORD_MAX_LENGTH
from app.services.booking_rules import (
    BOOKING_HORIZON,
    MIN_DURATION,
    PEAK_END,
    PEAK_GPU_HOUR_LIMIT,
    PEAK_START,
    PEAK_WEEKDAYS,
    SLOT,
)
from app.services.jobs import REMINDER_LEAD
from app.services.reservations import MAX_ACTIVE_RESERVATIONS

KB_DIR = Path(__file__).resolve().parents[2] / "kb"
# Backticked words in the documents that look like error slugs but are not.
NOT_ERROR_SLUGS = frozenset({"nvidia-smi"})
SLUG = re.compile(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`")

GPU_DISPLAY_NAMES = {
    GpuModel.RTX_4090: "RTX 4090",
    GpuModel.RTX_6000_ADA: "RTX 6000 Ada",
    GpuModel.L40S: "L40S",
    GpuModel.A100_40GB: "A100 40GB",
    GpuModel.A100_80GB: "A100 80GB",
    GpuModel.H100_80GB: "H100 80GB",
}


def _default(field: str) -> Any:
    return Settings.model_fields[field].default


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds()) // 60


def _expected() -> list[tuple[str, str]]:
    login_window = _minutes(timedelta(seconds=_default("login_email_window_seconds")))
    password_window = _minutes(timedelta(seconds=_default("password_change_window_seconds")))
    phrases = [
        ("fair-use-and-quota", f"{DEFAULT_WEEKLY_GPU_HOUR_QUOTA} GPU-hours per week"),
        ("fair-use-and-quota", "Monday 00:00 to the following Monday 00:00"),
        ("fair-use-and-quota", f"Pacific time ({_default('quota_timezone')})"),
        ("peak-hours", f"{PEAK_START:%H:%M} to {PEAK_END:%H:%M} on weekdays"),
        ("peak-hours", f"at most {PEAK_GPU_HOUR_LIMIT} GPU-hours inside the peak window"),
        ("booking-rules", f"The shortest booking is {_minutes(MIN_DURATION)} minutes"),
        ("booking-rules", f"which is {DEFAULT_MAX_BOOKING_HOURS} hours unless"),
        ("booking-rules", f"at most {BOOKING_HORIZON.days} days from now"),
        ("booking-rules", f"at most {MAX_ACTIVE_RESERVATIONS} active reservations"),
        ("booking-rules", f"more than {PEAK_GPU_HOUR_LIMIT} GPU-hours inside weekday peak hours"),
        ("approval-policy", f"up to {PURPOSE_MAX_LENGTH} characters"),
        (
            "security-and-accounts",
            f"at least {PASSWORD_MIN_LENGTH} characters long, and up to {PASSWORD_MAX_LENGTH}",
        ),
        (
            "security-and-accounts",
            f"After {_default('login_email_failure_limit')} failed sign-ins for the same email "
            f"address within {login_window} minutes",
        ),
        (
            "security-and-accounts",
            f"{_default('password_change_failure_limit')} per {password_window} minutes",
        ),
        (
            "security-and-accounts",
            f"for up to {_default('refresh_token_ttl_days')} days after you last used the app",
        ),
    ]
    phrases += [
        ("hardware-guide", f"{name}: {GPU_MEMORY_GB[model]} GB per GPU")
        for model, name in GPU_DISPLAY_NAMES.items()
    ]
    return phrases


def _text(document: str) -> str:
    return " ".join((KB_DIR / f"{document}.md").read_text(encoding="utf-8").split())


@pytest.mark.parametrize(("document", "phrase"), _expected())
def test_document_states_rule(document: str, phrase: str) -> None:
    assert phrase in _text(document)


def test_rules_described_in_words_match_constants() -> None:
    # The documents describe these in words; pin the constants they describe.
    assert timedelta(minutes=30) == SLOT
    assert "on the hour or the half hour" in _text("booking-rules")
    assert frozenset(range(5)) == PEAK_WEEKDAYS
    assert "Monday through Friday" in _text("peak-hours")
    assert timedelta(hours=1) == REMINDER_LEAD
    assert "about an hour before each approved booking starts" in _text("reservation-lifecycle")


def test_every_gpu_model_is_described() -> None:
    assert set(GPU_DISPLAY_NAMES) == set(GpuModel)


def _all_slugs() -> set[str]:
    slugs: set[str] = set()
    pending: list[type[DomainError]] = [DomainError]
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if "type_slug" in cls.__dict__:
            slugs.add(cls.type_slug)
    return slugs


def test_error_types_named_in_documents_exist() -> None:
    known = _all_slugs()
    named = {
        (path.stem, slug)
        for path in KB_DIR.glob("*.md")
        for slug in SLUG.findall(path.read_text(encoding="utf-8"))
        if slug not in NOT_ERROR_SLUGS
    }
    assert {slug for doc, slug in named if doc == "booking-rules"}, "no error types found"
    unknown = sorted((doc, slug) for doc, slug in named if slug not in known)
    assert not unknown
