"""Slot search: which (server, start, end) candidates come back, in what order, and
that they can actually be booked."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Final

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidSearchWindow, TooManyReservations
from app.models import GpuModel, ServerStatus, User, UserRole
from app.services import labs, slot_search
from app.services.reservations import NewReservation, create_reservation
from app.services.slot_search import SlotQuery
from tests.integration.reservation_helpers import (
    QUOTA_ZONE,
    add_lab,
    add_server,
    add_user,
    book,
)
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

H: Final = timedelta(hours=1)
# Monday 7 January 2030, 10:00 in Los Angeles.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
# Saturday 12 January 2030, 08:00 in Los Angeles: no peak hours all day.
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
# Tuesday 8 January 2030, 09:00 in Los Angeles: the start of peak hours.
TUESDAY_9AM: Final = datetime(2030, 1, 8, 17, 0, tzinfo=UTC)


def query(
    earliest: datetime = SATURDAY,
    latest: datetime | None = None,
    *,
    gpus: int = 1,
    minutes: int = 60,
    gpu_model: GpuModel | None = None,
    min_memory: int | None = None,
) -> SlotQuery:
    return SlotQuery(
        gpu_count=gpus,
        duration_minutes=minutes,
        earliest=earliest,
        latest=latest or earliest + 6 * H,
        gpu_model=gpu_model,
        min_gpu_memory_gb=min_memory,
    )


async def search(
    db: AsyncSession, user: User, q: SlotQuery, now: datetime = NOW
) -> list[tuple[str, datetime]]:
    results = await slot_search.search_slots(db, user, q, now=now, quota_zone=QUOTA_ZONE)
    return [(r.server.hostname, r.starts_at) for r in results]


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


# --- what is free ---


async def test_earliest_free_starts_on_a_free_server(
    db_session: AsyncSession, student: User
) -> None:
    await add_server(db_session, await add_lab(db_session), "a100-node-01")

    found = await search(db_session, student, query())

    # One server: three non-overlapping one-hour slots, earliest first.
    assert found == [
        ("a100-node-01", SATURDAY),
        ("a100-node-01", SATURDAY + H),
        ("a100-node-01", SATURDAY + 2 * H),
    ]


async def test_busy_gpus_push_the_start_later(db_session: AsyncSession, student: User) -> None:
    server = await add_server(db_session, await add_lab(db_session), gpu_count=2)
    other = await add_user(db_session, "other@example.edu")
    await book(db_session, other, server, SATURDAY, now=NOW, hours=2)

    found = await search(db_session, student, query(gpus=2))

    assert found[0] == ("a100-node-01", SATURDAY + 2 * H)


async def test_results_never_overlap_on_one_server(db_session: AsyncSession, student: User) -> None:
    await add_server(db_session, await add_lab(db_session))

    results = await slot_search.search_slots(
        db_session, student, query(minutes=90), now=NOW, quota_zone=QUOTA_ZONE
    )

    for earlier, later in pairwise(results):
        assert later.starts_at >= earlier.ends_at


async def test_results_end_by_latest(db_session: AsyncSession, student: User) -> None:
    await add_server(db_session, await add_lab(db_session))

    results = await slot_search.search_slots(
        db_session,
        student,
        query(latest=SATURDAY + 150 * timedelta(minutes=1), minutes=60),
        now=NOW,
        quota_zone=QUOTA_ZONE,
    )

    # 08:00-10:30: the last one-hour booking that fits starts at 09:30, but 08:00 is
    # taken first and 09:00 is the next start that does not overlap it.
    assert [r.starts_at for r in results] == [SATURDAY, SATURDAY + H]
    assert all(r.ends_at <= SATURDAY + 150 * timedelta(minutes=1) for r in results)


async def test_a_found_slot_can_be_booked(db_session: AsyncSession, student: User) -> None:
    await add_server(db_session, await add_lab(db_session), gpu_count=4)

    [first, *_] = await slot_search.search_slots(
        db_session, student, query(gpus=3, minutes=120), now=NOW, quota_zone=QUOTA_ZONE
    )
    booked = await create_reservation(
        db_session,
        student,
        NewReservation(first.server.id, 3, first.starts_at, first.ends_at, "From search"),
        now=NOW,
        quota_zone=QUOTA_ZONE,
    )

    assert (booked.starts_at, booked.ends_at) == (first.starts_at, first.ends_at)


# --- which servers ---


async def test_filters_by_model_memory_and_gpu_count(
    db_session: AsyncSession, student: User
) -> None:
    lab = await add_lab(db_session)
    await add_server(db_session, lab, "l40s-node-01", gpu_model=GpuModel.L40S, gpu_count=8)
    await add_server(db_session, lab, "a100-40-node-01", gpu_model=GpuModel.A100_40GB)
    await add_server(db_session, lab, "a100-80-small", gpu_model=GpuModel.A100_80GB, gpu_count=2)
    await add_server(db_session, lab, "a100-80-big", gpu_model=GpuModel.A100_80GB, gpu_count=8)

    by_memory = {h for h, _ in await search(db_session, student, query(gpus=4, min_memory=80))}
    by_model = {h for h, _ in await search(db_session, student, query(gpu_model=GpuModel.L40S))}

    assert by_memory == {"a100-80-big"}
    assert by_model == {"l40s-node-01"}


async def test_servers_that_cannot_be_booked_are_left_out(
    db_session: AsyncSession, student: User
) -> None:
    open_lab = await add_lab(db_session, "Open Lab")
    closed_lab = await add_lab(db_session, "Closed Lab")
    await add_server(db_session, open_lab, "open-01")
    await add_server(db_session, open_lab, "maint-01", status=ServerStatus.MAINTENANCE)
    await add_server(db_session, open_lab, "retired-01", status=ServerStatus.RETIRED)
    await add_server(db_session, closed_lab, "closed-01")
    admin = await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)
    await labs.update_lab(db_session, admin, closed_lab.id, is_active=False)
    await db_session.commit()

    assert {h for h, _ in await search(db_session, student, query())} == {"open-01"}
    # Admins see hidden servers elsewhere, but cannot book them either.
    assert {h for h, _ in await search(db_session, admin, query())} == {"open-01"}


async def test_servers_whose_limit_is_shorter_than_the_job_are_left_out(
    db_session: AsyncSession, student: User
) -> None:
    lab = await add_lab(db_session)
    await add_server(db_session, lab, "short-01", max_booking_hours=2)
    await add_server(db_session, lab, "long-01", max_booking_hours=12)

    found = await search(db_session, student, query(minutes=180, latest=SATURDAY + 10 * H))

    assert {h for h, _ in found} == {"long-01"}


# --- ranking ---


async def test_same_start_ranks_open_then_smaller_memory_then_tighter_fit(
    db_session: AsyncSession, student: User
) -> None:
    lab = await add_lab(db_session)
    await add_server(
        db_session, lab, "h100-restricted", gpu_model=GpuModel.H100_80GB, requires_approval=True
    )
    await add_server(db_session, lab, "a100-80-8gpu", gpu_model=GpuModel.A100_80GB, gpu_count=8)
    await add_server(db_session, lab, "a100-80-2gpu", gpu_model=GpuModel.A100_80GB, gpu_count=2)
    await add_server(db_session, lab, "l40s-4gpu", gpu_model=GpuModel.L40S, gpu_count=4)

    found = await search(db_session, student, query(gpus=2, latest=SATURDAY + H))

    assert [h for h, _ in found] == ["l40s-4gpu", "a100-80-2gpu", "a100-80-8gpu", "h100-restricted"]


async def test_at_most_three_per_server_and_twenty_in_all(
    db_session: AsyncSession, student: User
) -> None:
    lab = await add_lab(db_session)
    for i in range(8):
        await add_server(db_session, lab, f"node-{i:02}")

    found = await search(db_session, student, query(latest=SATURDAY + 12 * H))

    assert len(found) == slot_search.MAX_RESULTS == 20
    per_server: dict[str, int] = {}
    for hostname, _ in found:
        per_server[hostname] = per_server.get(hostname, 0) + 1
    assert max(per_server.values()) == slot_search.MAX_PER_SERVER == 3
    assert [start for _, start in found] == sorted(start for _, start in found)


# --- booking rules ---


async def test_peak_hour_limit_moves_large_jobs_out_of_peak(
    db_session: AsyncSession, student: User
) -> None:
    """4 GPUs for 2 hours is 8 GPU-hours. Inside peak hours (09:00-18:00) a booking may
    use at most 4, so the first start that fits is 17:00: one hour of it is peak, 4
    GPU-hours exactly."""
    await add_server(db_session, await add_lab(db_session), gpu_count=4)

    found = await search(
        db_session, student, query(TUESDAY_9AM, TUESDAY_9AM + 12 * H, gpus=4, minutes=120)
    )

    assert found[0][1] == TUESDAY_9AM + 8 * H


async def test_quota_moves_results_to_a_week_with_room(
    db_session: AsyncSession, two_hours_left: User
) -> None:
    """The user has 2 GPU-hours left this week, and the search spans Sunday night into
    next week. 3 GPUs for an hour does not fit this week; starting at 23:30 puts only
    1.5 GPU-hours in it, which does."""
    sunday_evening = datetime(2030, 1, 14, 2, 0, tzinfo=UTC)  # Sunday 18:00 in LA
    found = await search(
        db_session,
        two_hours_left,
        query(sunday_evening, sunday_evening + 10 * H, gpus=3, minutes=60),
    )

    next_week = datetime(2030, 1, 14, 8, 0, tzinfo=UTC)  # Monday 00:00 in LA
    assert found[0][1] == next_week - timedelta(minutes=30)
    assert all(start >= next_week - timedelta(minutes=30) for _, start in found)


@pytest.fixture
async def two_hours_left(db_session: AsyncSession) -> User:
    user = await add_user(db_session, "quota@example.edu", quota=6)
    server = await add_server(db_session, await add_lab(db_session), gpu_count=4)
    await book(db_session, user, server, SATURDAY, now=NOW, hours=1, gpus=4)
    return user


async def test_starts_beyond_the_booking_horizon_are_left_out(
    db_session: AsyncSession, student: User
) -> None:
    await add_server(db_session, await add_lab(db_session))
    horizon = NOW + timedelta(days=14)

    found = await search(db_session, student, query(horizon - 2 * H, horizon + 6 * H))

    assert found and all(start <= horizon for _, start in found)


async def test_an_earliest_in_the_past_starts_after_now(
    db_session: AsyncSession, student: User
) -> None:
    await add_server(db_session, await add_lab(db_session))

    found = await search(db_session, student, query(NOW - 3 * H, NOW + 3 * H))

    assert found[0][1] == NOW + timedelta(minutes=30)


async def test_holding_four_reservations_is_refused_up_front(
    db_session: AsyncSession, student: User
) -> None:
    server = await add_server(db_session, await add_lab(db_session), gpu_count=8)
    for day in range(4):
        await book(db_session, student, server, SATURDAY + timedelta(days=day), now=NOW)

    with pytest.raises(TooManyReservations):
        await search(db_session, student, query())


@pytest.mark.parametrize(
    ("earliest", "latest", "minutes"),
    [
        (SATURDAY, SATURDAY + timedelta(days=14, minutes=30), 60),  # window over 14 days
        (SATURDAY, SATURDAY + H, 90),  # window shorter than the job
        (SATURDAY, SATURDAY, 30),  # empty window
        (NOW - 5 * H, NOW - H, 30),  # entirely in the past
    ],
)
async def test_invalid_windows(
    db_session: AsyncSession, student: User, earliest: datetime, latest: datetime, minutes: int
) -> None:
    with pytest.raises(InvalidSearchWindow):
        await search(db_session, student, query(earliest, latest, minutes=minutes))


# --- through the API ---


async def test_search_through_the_api(api_client: AsyncClient, db_session: AsyncSession) -> None:
    await create_user(db_session, "student@example.edu")
    await add_server(db_session, await add_lab(db_session), "a100-node-01")
    headers = bearer(await login(api_client, "student@example.edu"))
    earliest = datetime.now(UTC) + timedelta(days=1)

    response = await api_client.post(
        "/api/v1/search/slots",
        json={
            "gpu_count": 1,
            "duration_minutes": 60,
            "earliest": earliest.isoformat(),
            "latest": (earliest + 12 * H).isoformat(),
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    [first, *_] = response.json()["items"]
    assert first["server"]["hostname"] == "a100-node-01"
    assert first["server"]["requires_approval"] is False
    assert first["lab"]["timezone"] == "America/Los_Angeles"
    starts = datetime.fromisoformat(first["starts_at"])
    assert datetime.fromisoformat(first["ends_at"]) - starts == H
    assert starts >= earliest
    assert first["free_gpus"] == 4


@pytest.mark.parametrize(
    "change",
    [
        {"duration_minutes": 45},
        {"duration_minutes": 0},
        {"gpu_count": 0},
        {"earliest": "2030-01-12T08:00:00"},
        {"gpu_model": "TPU"},
    ],
)
async def test_api_validation(
    api_client: AsyncClient, db_session: AsyncSession, change: dict[str, object]
) -> None:
    await create_user(db_session, "student@example.edu")
    headers = bearer(await login(api_client, "student@example.edu"))
    body = {
        "gpu_count": 1,
        "duration_minutes": 60,
        "earliest": "2030-01-12T08:00:00-08:00",
        "latest": "2030-01-12T20:00:00-08:00",
    } | change

    response = await api_client.post("/api/v1/search/slots", json=body, headers=headers)

    assert response.status_code == 422


async def test_api_window_error_is_a_problem(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(db_session, "student@example.edu")
    headers = bearer(await login(api_client, "student@example.edu"))
    earliest = datetime.now(UTC) + timedelta(days=1)

    response = await api_client.post(
        "/api/v1/search/slots",
        json={
            "gpu_count": 1,
            "duration_minutes": 60,
            "earliest": earliest.isoformat(),
            "latest": (earliest + timedelta(days=20)).isoformat(),
        },
        headers=headers,
    )

    assert_problem(response, 422, "invalid-search-window")
