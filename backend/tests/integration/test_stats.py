"""Dashboard numbers and utilization."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidUtilizationWindow, LabNotFound
from app.models import Server, ServerStatus, User, UserRole
from app.services import reservations, servers, stats
from app.services.booking_rules import Interval
from tests.integration.reservation_helpers import QUOTA_ZONE, add_lab, add_server, add_user, book
from tests.integration.test_admin_users import bearer, create_user, login

pytestmark = pytest.mark.integration

H: Final = timedelta(hours=1)
# Monday 7 January 2030, 10:00 in Los Angeles.
NOW: Final = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
# Saturday 12 January 2030, 08:00 in Los Angeles.
SATURDAY: Final = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)
DAY: Final = Interval(
    datetime(2030, 1, 12, 8, 0, tzinfo=UTC), datetime(2030, 1, 13, 8, 0, tzinfo=UTC)
)


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), "a100-node-01", gpu_count=4)


def _row(report: stats.Utilization, hostname: str) -> stats.ServerUtilization:
    [row] = [r for r in report.servers if r.hostname == hostname]
    return row


# --- utilization ---


async def test_booked_and_pending_hours_inside_the_window(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    lab = await add_lab(db_session, "Restricted Lab")
    restricted = await add_server(db_session, lab, "h100-node-01", requires_approval=True)
    await book(db_session, student, server, SATURDAY, now=NOW, hours=2, gpus=2)  # 4 booked
    cancelled = await book(db_session, student, server, SATURDAY + 3 * H, now=NOW, gpus=4)
    await reservations.cancel_reservation(db_session, student, cancelled.id, now=NOW)
    await db_session.commit()
    await book(db_session, student, restricted, SATURDAY, now=NOW, hours=1, gpus=1)  # 1 pending

    report = await stats.utilization(db_session, admin, window=DAY)

    row = _row(report, "a100-node-01")
    assert (row.available_gpu_hours, row.booked_gpu_hours, row.pending_gpu_hours) == (96, 4, 0)
    assert row.utilization == pytest.approx(4 / 96)
    pending_row = _row(report, "h100-node-01")
    assert (pending_row.booked_gpu_hours, pending_row.pending_gpu_hours) == (0, 1)
    assert report.totals.available_gpu_hours == 192
    assert report.totals.booked_gpu_hours == 4
    assert report.totals.pending_gpu_hours == 1


async def test_bookings_are_clipped_to_the_window(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    # 23:00-03:00 LA time, Saturday into Sunday: 1 hour inside the Saturday window.
    late = datetime(2030, 1, 13, 7, 0, tzinfo=UTC)
    await book(db_session, student, server, late, now=NOW, hours=4, gpus=1)

    report = await stats.utilization(db_session, admin, window=DAY)

    assert _row(report, "a100-node-01").booked_gpu_hours == 1


async def test_ending_early_counts_only_the_time_used(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await book(db_session, student, server, SATURDAY, now=NOW, hours=4, gpus=2)
    await reservations.cancel_reservation(
        db_session, student, booked.id, now=SATURDAY + 50 * timedelta(minutes=1)
    )
    await db_session.commit()

    report = await stats.utilization(db_session, admin, window=DAY)

    # Ended at the next half hour after 50 minutes: 1 hour on 2 GPUs.
    assert _row(report, "a100-node-01").booked_gpu_hours == 2


async def test_retired_servers_are_left_out_and_maintenance_kept(
    db_session: AsyncSession, admin: User
) -> None:
    lab = await add_lab(db_session)
    await add_server(db_session, lab, "maint-01", status=ServerStatus.MAINTENANCE)
    await add_server(db_session, lab, "retired-01", status=ServerStatus.RETIRED)

    report = await stats.utilization(db_session, admin, window=DAY)

    assert [r.hostname for r in report.servers] == ["maint-01"]


async def test_utilization_by_lab(db_session: AsyncSession, admin: User, server: Server) -> None:
    other_lab = await add_lab(db_session, "NLP Lab")
    await add_server(db_session, other_lab, "nlp-01")

    report = await stats.utilization(db_session, admin, window=DAY, lab_id=other_lab.id)

    assert [r.hostname for r in report.servers] == ["nlp-01"]


async def test_utilization_of_an_unknown_lab(db_session: AsyncSession, admin: User) -> None:
    with pytest.raises(LabNotFound):
        await stats.utilization(db_session, admin, window=DAY, lab_id=uuid.uuid4())


async def test_an_empty_window_has_zero_utilization(db_session: AsyncSession, admin: User) -> None:
    report = await stats.utilization(db_session, admin, window=DAY)

    assert report.servers == []
    assert report.totals.utilization == 0


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (NOW, NOW),
        (NOW, NOW - H),
        (NOW, NOW + timedelta(days=31, hours=1)),
    ],
)
def test_utilization_window_limits(start: datetime, end: datetime) -> None:
    with pytest.raises(InvalidUtilizationWindow):
        stats.utilization_window(start, end, now=NOW)


def test_utilization_window_defaults_to_the_last_seven_days() -> None:
    window = stats.utilization_window(None, None, now=NOW)

    assert (window.start, window.end) == (NOW - timedelta(days=7), NOW)


# --- overview ---


async def test_student_overview(db_session: AsyncSession, student: User, server: Server) -> None:
    lab = await add_lab(db_session, "Restricted Lab")
    restricted = await add_server(db_session, lab, "h100-node-01", requires_approval=True)
    first = await book(db_session, student, server, SATURDAY, now=NOW)
    second = await book(db_session, student, restricted, SATURDAY + 2 * H, now=NOW)
    third = await book(db_session, student, server, SATURDAY + timedelta(days=1), now=NOW)
    cancelled = await book(db_session, student, server, SATURDAY + timedelta(days=2), now=NOW)
    await reservations.cancel_reservation(db_session, student, cancelled.id, now=NOW)
    await db_session.commit()

    overview = await stats.overview(db_session, student, now=NOW, quota_zone=QUOTA_ZONE)

    assert [r.id for r in overview.upcoming] == [first.id, second.id, third.id]
    assert overview.my_pending == 1
    # Saturday, Saturday (pending counts), and Sunday morning are all this quota week.
    assert overview.quota.used_minutes == 3 * 60
    assert overview.admin is None


async def test_upcoming_includes_running_and_stops_at_three(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    running = await book(db_session, student, server, SATURDAY, now=NOW, hours=2)
    later = [
        await book(db_session, student, server, SATURDAY + timedelta(days=d), now=NOW)
        for d in (1, 2, 3)
    ]

    overview = await stats.overview(db_session, student, now=SATURDAY + H, quota_zone=QUOTA_ZONE)

    assert [r.id for r in overview.upcoming] == [running.id, later[0].id, later[1].id]


async def test_admin_overview(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    lab = await add_lab(db_session, "Restricted Lab")
    restricted = await add_server(db_session, lab, "h100-node-01", requires_approval=True)
    await add_server(db_session, lab, "retired-01", status=ServerStatus.RETIRED)
    await book(db_session, student, server, SATURDAY, now=NOW, hours=2, gpus=3)
    await book(db_session, student, restricted, SATURDAY + timedelta(days=1), now=NOW)

    overview = await stats.overview(db_session, admin, now=SATURDAY + H, quota_zone=QUOTA_ZONE)

    assert overview.admin is not None
    assert overview.admin.pending_approvals == 1
    assert overview.admin.gpus_in_use == 3
    assert overview.admin.gpus_total == 8  # the retired server's GPUs are not counted
    # This quota week, Monday 7 to Monday 14 January: 3 GPUs x 2 hours booked of
    # 8 GPUs x 168 hours. The pending request is not counted as booked.
    assert overview.admin.utilization_this_week == pytest.approx(6 / (8 * 168))


async def test_gpus_in_maintenance_count_toward_the_total(
    db_session: AsyncSession, admin: User, server: Server
) -> None:
    await servers.update_server(db_session, server.id, status=ServerStatus.MAINTENANCE, now=NOW)
    await db_session.commit()

    overview = await stats.overview(db_session, admin, now=NOW, quota_zone=QUOTA_ZONE)

    assert overview.admin is not None and overview.admin.gpus_total == 4


# --- API ---


async def test_overview_through_the_api(api_client: AsyncClient, db_session: AsyncSession) -> None:
    await create_user(db_session, "student@example.edu")
    headers = bearer(await login(api_client, "student@example.edu"))

    response = await api_client.get("/api/v1/stats/overview", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quota"]["quota_hours"] == 48
    assert body["upcoming"] == []
    assert body["my_pending"] == 0
    assert body["admin"] is None


async def test_utilization_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    await add_server(db_session, await add_lab(db_session), "a100-node-01", gpu_count=2)
    headers = bearer(await login(api_client, "admin@example.edu"))
    end = datetime.now(UTC)

    response = await api_client.get(
        "/api/v1/stats/utilization",
        params={"from": (end - timedelta(days=1)).isoformat(), "to": end.isoformat()},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    [row] = body["servers"]
    assert row["hostname"] == "a100-node-01"
    assert row["available_gpu_hours"] == pytest.approx(48)
    assert row["lab"]["name"] == "Vision Lab"
    assert body["totals"]["booked_gpu_hours"] == 0


async def test_utilization_window_error_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "admin@example.edu"))

    response = await api_client.get(
        "/api/v1/stats/utilization",
        params={"from": "2030-01-01T00:00:00Z", "to": "2030-03-01T00:00:00Z"},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["type"].endswith("/invalid-utilization-window")
