"""POST /reservations: every booking rule, through the API."""

from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, GpuAllocation, Reservation, ServerStatus, User, UserRole
from app.services.booking_rules import floor_to_grid
from tests.integration.reservation_helpers import (
    add_lab,
    add_server,
    booking_body,
    next_saturday,
    next_weekday_at,
)
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

RESERVATIONS = "/api/v1/reservations"


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await create_user(db_session, "student@example.edu", full_name="Ada Lovelace")


@pytest.fixture
async def headers(api_client: AsyncClient, student: User) -> dict[str, str]:
    return bearer(await login(api_client, student.email))


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


@pytest.fixture
async def lab(db_session: AsyncSession) -> Any:
    return await add_lab(db_session)


@pytest.fixture
async def server(db_session: AsyncSession, lab: Any) -> Any:
    return await add_server(db_session, lab)


def _now() -> datetime:
    return datetime.now(UTC)


async def book(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(RESERVATIONS, json=body, headers=headers)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


# --- success ---


async def test_booking_on_an_open_server_is_approved(
    api_client: AsyncClient,
    headers: dict[str, str],
    server: Any,
    student: User,
    db_session: AsyncSession,
) -> None:
    start = next_saturday(_now())

    created = await book(api_client, headers, booking_body(server.id, start, gpu_count=2))

    assert created["status"] == "approved"
    assert created["gpu_count"] == 2
    assert created["gpu_indexes"] == [0, 1]
    assert datetime.fromisoformat(created["starts_at"]) == start
    assert created["server"]["hostname"] == "a100-node-01"
    assert created["lab"]["timezone"] == "America/Los_Angeles"
    assert created["user"]["id"] == str(student.id)
    assert created["created_via"] == "web"

    allocations = list(await db_session.scalars(select(GpuAllocation)))
    assert len(allocations) == 2
    assert all(a.active and a.starts_at == start for a in allocations)
    entry = await db_session.scalar(select(AuditLog))
    assert entry is not None
    assert (entry.action, entry.actor_id) == ("reservation.created", student.id)
    assert str(entry.entity_id) == created["id"]


async def test_booking_on_an_approval_server_is_pending(
    api_client: AsyncClient, headers: dict[str, str], lab: Any, db_session: AsyncSession
) -> None:
    restricted = await add_server(db_session, lab, "h100-node-01", requires_approval=True)

    created = await book(api_client, headers, booking_body(restricted.id, next_saturday(_now())))

    assert created["status"] == "pending"
    # Pending reservations already hold their GPUs.
    assert await db_session.scalar(select(func.count()).where(GpuAllocation.active)) == 1


async def test_times_with_any_offset_are_stored_in_utc(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_saturday(_now())
    body = booking_body(server.id, start)
    body["starts_at"] = start.astimezone(timezone(timedelta(hours=9))).isoformat()

    created = await book(api_client, headers, body)

    assert created["starts_at"].endswith("Z") or created["starts_at"].endswith("+00:00")
    assert datetime.fromisoformat(created["starts_at"]) == start


async def test_lowest_free_gpus_are_allocated(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_saturday(_now())
    await book(api_client, headers, booking_body(server.id, start, gpu_count=1))

    second = await book(api_client, headers, booking_body(server.id, start, gpu_count=2))

    assert second["gpu_indexes"] == [1, 2]


async def test_back_to_back_bookings_share_gpus(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_saturday(_now())
    await book(api_client, headers, booking_body(server.id, start, hours=2, gpu_count=4))

    later = await book(
        api_client, headers, booking_body(server.id, start + timedelta(hours=2), gpu_count=4)
    )

    assert later["gpu_indexes"] == [0, 1, 2, 3]


# --- request shape (422) ---


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"starts_at": "2030-01-12T10:00:00"}, "starts_at"),  # no offset
        ({"gpu_count": 0}, "gpu_count"),
        ({"gpu_count": 17}, "gpu_count"),
        ({"purpose": "   "}, "purpose"),
        ({"purpose": "x" * 501}, "purpose"),
        ({"user_id": "00000000-0000-0000-0000-000000000000"}, "user_id"),
    ],
)
async def test_schema_validation(
    api_client: AsyncClient,
    headers: dict[str, str],
    server: Any,
    change: dict[str, Any],
    field: str,
) -> None:
    body = {**booking_body(server.id, next_saturday(_now())), **change}

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert response.status_code == 422, response.text
    assert field in {error["field"] for error in response.json()["errors"]}


@pytest.mark.parametrize("hours", [0, -1])
async def test_end_must_be_after_start(
    api_client: AsyncClient, headers: dict[str, str], server: Any, hours: int
) -> None:
    body = booking_body(server.id, next_saturday(_now()), hours=hours)

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 422, "invalid-time-range")


# --- request rules that read the server (422) ---


@pytest.mark.parametrize(
    ("start_shift", "hours"),
    [(timedelta(minutes=15), 2), (timedelta(0), 1.25), (timedelta(seconds=1), 2)],
)
async def test_times_must_be_on_the_half_hour_grid(
    api_client: AsyncClient,
    headers: dict[str, str],
    server: Any,
    start_shift: timedelta,
    hours: float,
) -> None:
    body = booking_body(server.id, next_saturday(_now()) + start_shift, hours=hours)

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 422, "invalid-time-grid")


# booking-too-short cannot be reached through the API: start and end are both on the
# 30-minute grid and end is after start, so every accepted range is at least 30
# minutes. Short ranges are reported as invalid-time-grid or invalid-time-range (above).
# The rule itself stays as a defensive check and is covered in test_booking_rules.


async def test_start_must_be_in_the_future(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    body = booking_body(server.id, floor_to_grid(_now()))

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 422, "start-in-past")


async def test_start_at_most_fourteen_days_ahead(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    body = booking_body(server.id, next_saturday(_now() + timedelta(days=14)))

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 422, "start-too-far")


async def test_duration_at_most_max_booking_hours(
    api_client: AsyncClient, headers: dict[str, str], lab: Any, db_session: AsyncSession
) -> None:
    short = await add_server(db_session, lab, "rtx-node-01", max_booking_hours=3)
    start = next_saturday(_now())

    await book(api_client, headers, booking_body(short.id, start, hours=3))
    response = await api_client.post(
        RESERVATIONS,
        json=booking_body(short.id, start + timedelta(hours=4), hours=3.5),
        headers=headers,
    )

    assert_problem(response, 422, "booking-too-long")
    assert "at most 3 hours" in response.json()["detail"]


async def test_gpu_count_at_most_the_servers(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    body = booking_body(server.id, next_saturday(_now()), gpu_count=5)

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 422, "too-many-gpus")


async def test_peak_hours_limit(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_weekday_at(_now(), time(9, 0))

    # 2 GPUs x 2 hours = 4 GPU-hours: allowed.
    await book(api_client, headers, booking_body(server.id, start, hours=2, gpu_count=2))
    # 3 GPUs x 1.5 hours = 4.5 GPU-hours: refused.
    response = await api_client.post(
        RESERVATIONS,
        json=booking_body(server.id, start + timedelta(hours=3), hours=1.5, gpu_count=3),
        headers=headers,
    )

    assert_problem(response, 422, "peak-hours-limit")


# --- server state (404 / 409) ---


async def test_unknown_server(api_client: AsyncClient, headers: dict[str, str]) -> None:
    body = booking_body("00000000-0000-0000-0000-000000000000", next_saturday(_now()))

    response = await api_client.post(RESERVATIONS, json=body, headers=headers)

    assert_problem(response, 404, "server-not-found")


async def test_students_cannot_book_hidden_servers(
    api_client: AsyncClient, headers: dict[str, str], lab: Any, db_session: AsyncSession
) -> None:
    retired = await add_server(db_session, lab, "old-node-01", status=ServerStatus.RETIRED)
    closed_lab = await add_lab(db_session, "Closed Lab", is_active=False)
    hidden = await add_server(db_session, closed_lab, "closed-node-01")

    for target in (retired, hidden):
        response = await api_client.post(
            RESERVATIONS, json=booking_body(target.id, next_saturday(_now())), headers=headers
        )
        assert_problem(response, 404, "server-not-found")


async def test_servers_in_maintenance_cannot_be_booked(
    api_client: AsyncClient, headers: dict[str, str], lab: Any, db_session: AsyncSession
) -> None:
    server = await add_server(db_session, lab, "maint-node-01", status=ServerStatus.MAINTENANCE)

    response = await api_client.post(
        RESERVATIONS, json=booking_body(server.id, next_saturday(_now())), headers=headers
    )

    assert_problem(response, 409, "server-unavailable")


async def test_admins_see_hidden_servers_but_cannot_book_them(
    api_client: AsyncClient, admin_headers: dict[str, str], lab: Any, db_session: AsyncSession
) -> None:
    retired = await add_server(db_session, lab, "old-node-01", status=ServerStatus.RETIRED)
    closed_lab = await add_lab(db_session, "Closed Lab", is_active=False)
    in_closed_lab = await add_server(db_session, closed_lab, "closed-node-01")

    for target in (retired, in_closed_lab):
        response = await api_client.post(
            RESERVATIONS,
            json=booking_body(target.id, next_saturday(_now())),
            headers=admin_headers,
        )
        assert_problem(response, 409, "server-unavailable")


# --- per-user state (409) ---


async def test_gpus_already_taken(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_saturday(_now())
    await book(api_client, headers, booking_body(server.id, start, gpu_count=3))

    response = await api_client.post(
        RESERVATIONS,
        json=booking_body(server.id, start + timedelta(hours=1), gpu_count=2),
        headers=headers,
    )

    assert_problem(response, 409, "gpu-unavailable")
    assert response.json()["detail"] == (
        "Only 1 of 2 requested GPUs is free on a100-node-01 for that time."
    )


async def test_at_most_four_active_reservations(
    api_client: AsyncClient, headers: dict[str, str], server: Any
) -> None:
    start = next_saturday(_now())
    for slot in range(4):
        await book(
            api_client, headers, booking_body(server.id, start + slot * timedelta(hours=1), hours=1)
        )

    response = await api_client.post(
        RESERVATIONS,
        json=booking_body(server.id, start + timedelta(hours=5), hours=1),
        headers=headers,
    )

    assert_problem(response, 409, "too-many-reservations")


async def test_weekly_quota(
    api_client: AsyncClient,
    headers: dict[str, str],
    server: Any,
    student: User,
    db_session: AsyncSession,
) -> None:
    student.weekly_gpu_hour_quota = 6
    await db_session.commit()
    start = next_saturday(_now())
    await book(api_client, headers, booking_body(server.id, start, hours=2, gpu_count=2))

    # 4 used + 2 x 1.5 = 7 > 6.
    response = await api_client.post(
        RESERVATIONS,
        json=booking_body(server.id, start + timedelta(hours=3), hours=1.5, gpu_count=2),
        headers=headers,
    )
    assert_problem(response, 409, "quota-exceeded")

    # 4 used + 2 x 1 = 6: exactly the quota.
    await book(
        api_client,
        headers,
        booking_body(server.id, start + timedelta(hours=3), gpu_count=2, hours=1),
    )


async def test_failed_booking_leaves_nothing_behind(
    api_client: AsyncClient, headers: dict[str, str], server: Any, db_session: AsyncSession
) -> None:
    start = next_saturday(_now())
    await book(api_client, headers, booking_body(server.id, start, gpu_count=4))

    response = await api_client.post(
        RESERVATIONS, json=booking_body(server.id, start, gpu_count=1), headers=headers
    )

    assert_problem(response, 409, "gpu-unavailable")
    assert await db_session.scalar(select(func.count()).select_from(Reservation)) == 1
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 1
