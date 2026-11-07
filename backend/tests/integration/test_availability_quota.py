"""GET /servers/{id}/availability and GET /me/quota."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidAvailabilityWindow, QuotaExceeded, ServerNotFound
from app.models import Server, ServerStatus, User, UserRole
from app.services import reservations, servers
from app.services.reservations import FreeRun, NewReservation, create_reservation
from tests.integration.reservation_helpers import add_lab, add_server, add_user
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

H = timedelta(hours=1)
M = timedelta(minutes=1)
# Monday 7 January 2030, 10:00 in Los Angeles. The quota week runs from Monday
# 7 January 08:00 UTC to Monday 14 January 08:00 UTC.
NOW = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
BAD_WINDOW = "invalid-availability-window"


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), gpu_count=4)


async def _book(
    db: AsyncSession,
    user: User,
    server: Server,
    start: datetime,
    length: timedelta,
    gpus: int = 1,
) -> None:
    await create_reservation(
        db,
        user,
        NewReservation(
            server_id=server.id,
            gpu_count=gpus,
            starts_at=start,
            ends_at=start + length,
            purpose="Run",
        ),
        now=NOW,
    )
    await db.commit()


async def _availability(
    db: AsyncSession, viewer: User, server: Server, start: datetime, end: datetime
) -> list[FreeRun]:
    result = await reservations.server_availability(
        db, viewer, server.id, starts_at=start, ends_at=end, now=NOW
    )
    return result.runs


# --- availability ---


async def test_free_runs_reflect_bookings(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, SATURDAY, 2 * H, gpus=3)
    await _book(db_session, student, server, SATURDAY + H, 2 * H, gpus=1)

    runs = await _availability(db_session, student, server, SATURDAY - H, SATURDAY + 4 * H)

    assert runs == [
        FreeRun(SATURDAY - H, SATURDAY, 4),
        FreeRun(SATURDAY, SATURDAY + H, 1),
        FreeRun(SATURDAY + H, SATURDAY + 2 * H, 0),
        FreeRun(SATURDAY + 2 * H, SATURDAY + 3 * H, 3),
        FreeRun(SATURDAY + 3 * H, SATURDAY + 4 * H, 4),
    ]


async def test_pending_reservations_count_as_busy(db_session: AsyncSession, student: User) -> None:
    restricted = await add_server(
        db_session, await add_lab(db_session), "h100-node-01", gpu_count=2, requires_approval=True
    )
    await _book(db_session, student, restricted, SATURDAY, H, gpus=2)

    runs = await _availability(db_session, student, restricted, SATURDAY, SATURDAY + H)

    assert runs == [FreeRun(SATURDAY, SATURDAY + H, 0)]


async def test_cancelled_reservations_are_free(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, SATURDAY, H, gpus=4)
    [booked] = (
        await reservations.list_reservations(
            db_session, student, reservations.ReservationFilters(), offset=0, limit=1
        )
    ).items
    await reservations.cancel_reservation(db_session, student, booked.id, now=NOW)
    await db_session.commit()

    runs = await _availability(db_session, student, server, SATURDAY, SATURDAY + H)

    assert runs == [FreeRun(SATURDAY, SATURDAY + H, 4)]


async def test_window_is_widened_to_whole_slots(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    result = await reservations.server_availability(
        db_session,
        student,
        server.id,
        starts_at=SATURDAY + 10 * M,
        ends_at=SATURDAY + 50 * M,
        now=NOW,
    )

    assert (result.window.start, result.window.end) == (SATURDAY, SATURDAY + H)


async def test_default_window_is_seven_days_from_now(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    result = await reservations.server_availability(
        db_session, student, server.id, starts_at=None, ends_at=None, now=NOW + 10 * M
    )

    # The start is now, floored to the grid; the end is seven days after it.
    assert (result.window.start, result.window.end) == (NOW, NOW + timedelta(days=7))
    assert result.runs == [FreeRun(result.window.start, result.window.end, 4)]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (SATURDAY, SATURDAY),
        (SATURDAY, SATURDAY - H),
        (SATURDAY, SATURDAY + timedelta(days=14, minutes=1)),
    ],
)
async def test_invalid_windows(
    db_session: AsyncSession, student: User, server: Server, start: datetime, end: datetime
) -> None:
    with pytest.raises(InvalidAvailabilityWindow):
        await _availability(db_session, student, server, start, end)


async def test_fourteen_days_is_allowed(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    runs = await _availability(db_session, student, server, SATURDAY, SATURDAY + timedelta(days=14))
    assert len(runs) == 1


async def test_hidden_servers_are_not_found_for_students(
    db_session: AsyncSession, student: User
) -> None:
    retired = await add_server(
        db_session, await add_lab(db_session), "old-node-01", status=ServerStatus.RETIRED
    )

    with pytest.raises(ServerNotFound):
        await _availability(db_session, student, retired, SATURDAY, SATURDAY + H)


async def test_removed_gpus_are_not_counted(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await servers.update_server(db_session, server.id, gpu_count=2, now=NOW)
    await db_session.commit()

    runs = await _availability(db_session, student, server, SATURDAY, SATURDAY + H)

    assert runs == [FreeRun(SATURDAY, SATURDAY + H, 2)]


@pytest.fixture
async def headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "ada@example.edu")
    return bearer(await login(api_client, "ada@example.edu"))


async def test_availability_through_the_api(
    api_client: AsyncClient, headers: dict[str, str], server: Server
) -> None:
    response = await api_client.get(
        f"/api/v1/servers/{server.id}/availability",
        headers=headers,
        params={"from": SATURDAY.isoformat(), "to": (SATURDAY + H).isoformat()},
    )

    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["gpu_count"] == 4
    assert body["slot_minutes"] == 30
    assert body["timezone"] == "America/Los_Angeles"
    assert len(body["runs"]) == 1
    assert body["runs"][0]["free"] == 4
    assert datetime.fromisoformat(body["runs"][0]["starts_at"]) == SATURDAY


@pytest.mark.parametrize(
    ("params", "slug"),
    [
        ({"from": "2030-01-12T10:00:00"}, None),  # no offset: schema validation
        ({"from": "2030-01-12T10:00:00Z", "to": "2030-01-12T10:00:00Z"}, BAD_WINDOW),
        ({"from": "2030-01-12T10:00:00Z", "to": "2030-01-27T10:00:00Z"}, BAD_WINDOW),
    ],
)
async def test_availability_rejects_bad_windows(
    api_client: AsyncClient,
    headers: dict[str, str],
    server: Server,
    params: dict[str, str],
    slug: str | None,
) -> None:
    response = await api_client.get(
        f"/api/v1/servers/{server.id}/availability", headers=headers, params=params
    )

    assert response.status_code == 422, response.text
    if slug is not None:
        assert_problem(response, 422, slug)


async def test_availability_of_a_hidden_server_is_not_found(
    api_client: AsyncClient, headers: dict[str, str], db_session: AsyncSession
) -> None:
    retired = await add_server(
        db_session, await add_lab(db_session), "old-node-01", status=ServerStatus.RETIRED
    )

    response = await api_client.get(f"/api/v1/servers/{retired.id}/availability", headers=headers)

    assert_problem(response, 404, "server-not-found")


# --- quota ---


async def test_quota_status_counts_this_week_only(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    # 3 GPU-hours this week, half an hour of it on Sunday night before Monday 00:00.
    await _book(db_session, student, server, SATURDAY, H, gpus=2)
    sunday_late = datetime(2030, 1, 14, 7, 30, tzinfo=UTC)  # Sunday 23:30 in LA
    await _book(db_session, student, server, sunday_late, H, gpus=2)
    await _book(db_session, student, server, SATURDAY + timedelta(days=7), H, gpus=4)

    status = await reservations.quota_status(db_session, student, now=NOW)

    assert status.week.start == datetime(2030, 1, 7, 8, 0, tzinfo=UTC)
    assert status.week.end == datetime(2030, 1, 14, 8, 0, tzinfo=UTC)
    assert status.timezone == "America/Los_Angeles"
    assert status.used_minutes == 2 * 60 + 2 * 30
    assert status.quota_minutes == 48 * 60


async def test_full_quota_reports_zero_and_refuses_the_smallest_booking(
    db_session: AsyncSession, server: Server
) -> None:
    student = await add_user(db_session, "full@example.edu", quota=6)
    await _book(db_session, student, server, SATURDAY, 3 * H, gpus=2)

    status = await reservations.quota_status(db_session, student, now=NOW)
    assert status.remaining_minutes == 0

    with pytest.raises(QuotaExceeded):
        await _book(db_session, student, server, SATURDAY + 4 * H, 30 * M, gpus=1)


async def test_half_hours_are_reported_as_fractions(
    db_session: AsyncSession, server: Server
) -> None:
    student = await add_user(db_session, "half@example.edu", quota=3)
    await _book(db_session, student, server, SATURDAY, 90 * M, gpus=1)

    status = await reservations.quota_status(db_session, student, now=NOW)

    assert (status.used_minutes, status.remaining_minutes) == (90, 90)


async def test_quota_through_the_api(api_client: AsyncClient, headers: dict[str, str]) -> None:
    response = await api_client.get("/api/v1/me/quota", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_hours"] == 0
    assert body["quota_hours"] == 48
    assert body["remaining_hours"] == 48
    assert body["timezone"] == "America/Los_Angeles"
    start = datetime.fromisoformat(body["week_start"])
    end = datetime.fromisoformat(body["week_end"])
    assert start <= datetime.now(UTC) < end


def test_quota_hours_are_numbers_in_the_schema(running_app: FastAPI) -> None:
    schema = running_app.openapi()["components"]["schemas"]["QuotaRead"]["properties"]

    for field in ("used_hours", "quota_hours", "remaining_hours"):
        assert schema[field]["type"] == "number"


async def test_admins_have_quota_status_too(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "boss@example.edu"))

    response = await api_client.get("/api/v1/me/quota", headers=headers)

    assert response.status_code == 200
