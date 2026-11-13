"""Reading, listing, cancelling, approving, and rejecting reservations."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidReservationState, ReservationNotFound
from app.models import (
    AuditLog,
    GpuAllocation,
    Reservation,
    ReservationStatus,
    Server,
    ServerStatus,
    User,
    UserRole,
)
from app.services import labs, reservations, servers
from app.services.booking_rules import Interval, used_gpu_minutes
from app.services.reservations import NewReservation, create_reservation
from tests.integration.reservation_helpers import (
    QUOTA_ZONE,
    add_lab,
    add_server,
    add_user,
    booking_body,
    next_saturday,
)
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

H = timedelta(hours=1)
RESERVATIONS = "/api/v1/reservations"
# Monday 7 January 2030, 10:00 in Los Angeles; bookings are that Saturday.
NOW = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
SATURDAY = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)


def _request(server: Server, start: datetime = SATURDAY, hours: int = 2) -> NewReservation:
    return NewReservation(
        server_id=server.id,
        gpu_count=1,
        starts_at=start,
        ends_at=start + hours * H,
        purpose="Run",
    )


async def _book(
    db: AsyncSession, user: User, server: Server, start: datetime = SATURDAY, hours: int = 2
) -> Reservation:
    reservation = await create_reservation(
        db, user, _request(server, start, hours), now=NOW, quota_zone=QUOTA_ZONE
    )
    await db.commit()
    return reservation


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def other(db_session: AsyncSession) -> User:
    return await add_user(db_session, "other@example.edu")


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), gpu_count=4)


@pytest.fixture
async def restricted(db_session: AsyncSession) -> Server:
    lab = await add_lab(db_session, "NLP Lab")
    return await add_server(db_session, lab, "h100-node-01", requires_approval=True)


async def _allocations_active(db: AsyncSession, reservation_id: uuid.UUID) -> list[bool]:
    return list(
        await db.scalars(
            select(GpuAllocation.active)
            .where(GpuAllocation.reservation_id == reservation_id)
            .execution_options(populate_existing=True)
        )
    )


async def _last_audit(db: AsyncSession) -> AuditLog:
    entry = await db.scalar(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(1))
    assert entry is not None
    return entry


# --- cancel ---


async def test_cancel_before_start(db_session: AsyncSession, student: User, server: Server) -> None:
    booked = await _book(db_session, student, server)

    cancelled = await reservations.cancel_reservation(db_session, student, booked.id, now=NOW)
    await db_session.commit()

    assert cancelled.status is ReservationStatus.CANCELLED
    assert cancelled.ends_at == booked.ends_at
    assert await _allocations_active(db_session, booked.id) == [False]
    entry = await _last_audit(db_session)
    assert (entry.action, entry.actor_id) == ("reservation.cancelled", student.id)


async def test_cancel_exactly_at_start_cancels(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    """In progress means strictly after the start; at the start it is still a cancel."""
    booked = await _book(db_session, student, server)

    cancelled = await reservations.cancel_reservation(
        db_session, student, booked.id, now=booked.starts_at
    )

    assert cancelled.status is ReservationStatus.CANCELLED


async def test_cancel_while_running_ends_early(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    booked = await _book(db_session, student, server, hours=4)
    now = SATURDAY + H + timedelta(minutes=5)

    ended = await reservations.cancel_reservation(db_session, student, booked.id, now=now)
    await db_session.commit()

    assert ended.status is ReservationStatus.COMPLETED
    assert ended.ends_at == SATURDAY + H + timedelta(minutes=30)
    allocation = await db_session.scalar(
        select(GpuAllocation).where(GpuAllocation.reservation_id == booked.id)
    )
    assert allocation is not None
    assert (allocation.active, allocation.ends_at) == (False, ended.ends_at)
    entry = await _last_audit(db_session)
    assert entry.action == "reservation.ended_early"
    # `booked` is the same session object, so compare with the original end directly.
    assert entry.payload["original_ends_at"] == (SATURDAY + 4 * H).isoformat()


async def test_ending_in_the_last_half_hour_keeps_the_end(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)
    now = booked.ends_at - timedelta(minutes=10)

    ended = await reservations.cancel_reservation(db_session, student, booked.id, now=now)

    assert ended.status is ReservationStatus.COMPLETED
    assert ended.ends_at == booked.ends_at


async def test_ended_early_time_is_free_again(
    db_session: AsyncSession, student: User, other: User, server: Server
) -> None:
    booked = await _book(db_session, student, server, hours=4)
    for _ in range(3):  # fill the other GPUs
        await _book(db_session, other, server, hours=4)
    now = SATURDAY + H + timedelta(minutes=5)
    await reservations.cancel_reservation(db_session, student, booked.id, now=now)
    await db_session.commit()

    later = SATURDAY + H + timedelta(minutes=30)
    rebooked = await create_reservation(
        db_session, other, _request(server, later, hours=1), now=now, quota_zone=QUOTA_ZONE
    )

    assert [a.gpu.index for a in rebooked.allocations] == [0]


async def test_cancelling_pending_after_start_cancels_and_frees_quota(
    db_session: AsyncSession, student: User, restricted: Server
) -> None:
    booked = await _book(db_session, student, restricted)
    after_start = SATURDAY + H

    cancelled = await reservations.cancel_reservation(
        db_session, student, booked.id, now=after_start
    )
    await db_session.commit()

    assert cancelled.status is ReservationStatus.CANCELLED
    week = Interval(SATURDAY - 6 * 24 * H, SATURDAY + 24 * H)
    counted = await reservations._load_quota_bookings(db_session, student.id, week)
    assert counted == []
    assert used_gpu_minutes(week, counted) == 0


@pytest.mark.parametrize("status", [ReservationStatus.CANCELLED, ReservationStatus.REJECTED])
async def test_terminal_reservations_cannot_be_cancelled(
    db_session: AsyncSession, student: User, server: Server, status: ReservationStatus
) -> None:
    booked = await _book(db_session, student, server)
    booked.status = status
    await db_session.commit()

    with pytest.raises(InvalidReservationState):
        await reservations.cancel_reservation(db_session, student, booked.id, now=NOW)


async def test_finished_approved_reservation_cannot_be_cancelled(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    with pytest.raises(InvalidReservationState, match="ended"):
        await reservations.cancel_reservation(db_session, student, booked.id, now=booked.ends_at)


async def test_others_cannot_cancel(
    db_session: AsyncSession, student: User, other: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    with pytest.raises(ReservationNotFound):
        await reservations.cancel_reservation(db_session, other, booked.id, now=NOW)


async def test_admin_can_cancel_anyones(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    cancelled = await reservations.cancel_reservation(db_session, admin, booked.id, now=NOW)
    await db_session.commit()

    assert cancelled.status is ReservationStatus.CANCELLED
    assert (await _last_audit(db_session)).actor_id == admin.id


# --- approve and reject ---


async def test_approve_pending(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    booked = await _book(db_session, student, restricted)

    approved = await reservations.approve_reservation(db_session, admin, booked.id, now=NOW)
    await db_session.commit()

    assert approved.status is ReservationStatus.APPROVED
    assert approved.decided_at == NOW
    assert approved.decision_reason is None
    assert await _allocations_active(db_session, booked.id) == [True]
    entry = await _last_audit(db_session)
    assert (entry.action, entry.actor_id) == ("reservation.approved", admin.id)


async def test_cannot_approve_after_start(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    booked = await _book(db_session, student, restricted)

    with pytest.raises(InvalidReservationState, match="already started"):
        await reservations.approve_reservation(db_session, admin, booked.id, now=booked.starts_at)


async def test_cannot_approve_twice(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    booked = await _book(db_session, student, restricted)
    await reservations.approve_reservation(db_session, admin, booked.id, now=NOW)
    await db_session.commit()

    with pytest.raises(InvalidReservationState, match="approved"):
        await reservations.approve_reservation(db_session, admin, booked.id, now=NOW)


async def test_reject_pending_releases_gpus(
    db_session: AsyncSession, student: User, admin: User, restricted: Server
) -> None:
    booked = await _book(db_session, student, restricted)

    rejected = await reservations.reject_reservation(
        db_session, admin, booked.id, reason="Maintenance that week", now=NOW
    )
    await db_session.commit()

    assert rejected.status is ReservationStatus.REJECTED
    assert rejected.decision_reason == "Maintenance that week"
    assert await _allocations_active(db_session, booked.id) == [False]
    entry = await _last_audit(db_session)
    assert entry.payload["reason"] == "Maintenance that week"


async def test_cannot_reject_approved(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    with pytest.raises(InvalidReservationState):
        await reservations.reject_reservation(db_session, admin, booked.id, reason="No", now=NOW)


# --- through the API ---


@pytest.fixture
async def student_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "ada@example.edu", full_name="Ada")
    return bearer(await login(api_client, "ada@example.edu"))


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "boss@example.edu"))


async def _post(
    client: AsyncClient, path: str, headers: dict[str, str], json: Any = None
) -> dict[str, Any]:
    response = await client.post(path, headers=headers, json=json)
    assert response.status_code in (200, 201), response.text
    result: dict[str, Any] = response.json()
    return result


async def test_approval_flow_through_the_api(
    api_client: AsyncClient,
    student_headers: dict[str, str],
    admin_headers: dict[str, str],
    restricted: Server,
) -> None:
    start = next_saturday(datetime.now(UTC))
    first = await _post(
        api_client, RESERVATIONS, student_headers, booking_body(restricted.id, start)
    )
    second = await _post(
        api_client, RESERVATIONS, student_headers, booking_body(restricted.id, start + 3 * H)
    )

    approved = await _post(api_client, f"{RESERVATIONS}/{first['id']}/approve", admin_headers)
    rejected = await _post(
        api_client,
        f"{RESERVATIONS}/{second['id']}/reject",
        admin_headers,
        {"reason": "Booked for a class"},
    )

    assert approved["status"] == "approved"
    assert approved["decided_at"] is not None
    assert (rejected["status"], rejected["decision_reason"]) == ("rejected", "Booked for a class")


@pytest.mark.parametrize("body", [{}, {"reason": "  "}, {"reason": "x" * 501}])
async def test_reject_requires_a_reason(
    api_client: AsyncClient,
    student_headers: dict[str, str],
    admin_headers: dict[str, str],
    restricted: Server,
    body: dict[str, str],
) -> None:
    booked = await _post(
        api_client,
        RESERVATIONS,
        student_headers,
        booking_body(restricted.id, next_saturday(datetime.now(UTC))),
    )

    response = await api_client.post(
        f"{RESERVATIONS}/{booked['id']}/reject", headers=admin_headers, json=body
    )

    assert response.status_code == 422, response.text


async def test_cancel_through_the_api(
    api_client: AsyncClient, student_headers: dict[str, str], server: Server
) -> None:
    booked = await _post(
        api_client,
        RESERVATIONS,
        student_headers,
        booking_body(server.id, next_saturday(datetime.now(UTC))),
    )

    cancelled = await _post(api_client, f"{RESERVATIONS}/{booked['id']}/cancel", student_headers)
    again = await api_client.post(f"{RESERVATIONS}/{booked['id']}/cancel", headers=student_headers)

    assert cancelled["status"] == "cancelled"
    assert_problem(again, 409, "invalid-reservation-state")


# --- visibility ---


async def test_owner_keeps_access_after_the_lab_is_deactivated(
    api_client: AsyncClient,
    db_session: AsyncSession,
    student_headers: dict[str, str],
    admin: User,
    server: Server,
) -> None:
    booked = await _post(
        api_client,
        RESERVATIONS,
        student_headers,
        booking_body(server.id, next_saturday(datetime.now(UTC))),
    )
    await labs.update_lab(db_session, admin, server.lab_id, is_active=False)
    await db_session.commit()
    await create_user(db_session, "eve@example.edu")
    stranger = bearer(await login(api_client, "eve@example.edu"))

    # The server itself is now hidden from students.
    hidden = await api_client.get(f"/api/v1/servers/{server.id}", headers=student_headers)
    assert_problem(hidden, 404, "server-not-found")

    read = await api_client.get(f"{RESERVATIONS}/{booked['id']}", headers=student_headers)
    listed = await api_client.get(RESERVATIONS, headers=student_headers)
    assert read.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [booked["id"]]
    assert_problem(
        await api_client.get(f"{RESERVATIONS}/{booked['id']}", headers=stranger),
        404,
        "reservation-not-found",
    )
    assert_problem(
        await api_client.post(f"{RESERVATIONS}/{booked['id']}/cancel", headers=stranger),
        404,
        "reservation-not-found",
    )

    cancelled = await _post(api_client, f"{RESERVATIONS}/{booked['id']}/cancel", student_headers)
    assert cancelled["status"] == "cancelled"


async def test_owner_sees_reservations_on_a_retired_server(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)
    await servers.update_server(
        db_session, server.id, status=ServerStatus.RETIRED, now=booked.ends_at
    )
    await db_session.commit()

    assert (await reservations.get_reservation(db_session, student, booked.id)).id == booked.id


# --- listing ---


async def test_students_list_only_their_own(
    db_session: AsyncSession, student: User, other: User, admin: User, server: Server
) -> None:
    mine = await _book(db_session, student, server)
    theirs = await _book(db_session, other, server)

    own = await reservations.list_reservations(
        db_session, student, reservations.ReservationFilters(user_id=other.id), offset=0, limit=10
    )
    everyone = await reservations.list_reservations(
        db_session, admin, reservations.ReservationFilters(), offset=0, limit=10
    )
    filtered = await reservations.list_reservations(
        db_session, admin, reservations.ReservationFilters(user_id=other.id), offset=0, limit=10
    )

    assert [r.id for r in own.items] == [mine.id]
    assert {r.id for r in everyone.items} == {mine.id, theirs.id}
    assert [r.id for r in filtered.items] == [theirs.id]


async def test_list_filters_by_status_and_server(
    db_session: AsyncSession, student: User, server: Server, restricted: Server
) -> None:
    approved = await _book(db_session, student, server)
    pending = await _book(db_session, student, restricted)

    by_status = await reservations.list_reservations(
        db_session,
        student,
        reservations.ReservationFilters(status=ReservationStatus.PENDING),
        offset=0,
        limit=10,
    )
    by_server = await reservations.list_reservations(
        db_session,
        student,
        reservations.ReservationFilters(server_id=server.id),
        offset=0,
        limit=10,
    )

    assert [r.id for r in by_status.items] == [pending.id]
    assert [r.id for r in by_server.items] == [approved.id]


@pytest.mark.parametrize(
    ("window_start", "window_end", "expected"),
    [
        # The reservation is 16:00-18:00 UTC.
        (SATURDAY - H, SATURDAY, False),  # ends where it starts: no overlap
        (SATURDAY + 2 * H, SATURDAY + 3 * H, False),  # starts where it ends
        (SATURDAY - H, SATURDAY + timedelta(minutes=30), True),
        (SATURDAY + H, SATURDAY + 5 * H, True),
        (SATURDAY + H, SATURDAY + H + timedelta(minutes=30), True),  # inside
    ],
)
async def test_date_filter_means_overlap(
    db_session: AsyncSession,
    student: User,
    server: Server,
    window_start: datetime,
    window_end: datetime,
    expected: bool,
) -> None:
    await _book(db_session, student, server)

    listing = await reservations.list_reservations(
        db_session,
        student,
        reservations.ReservationFilters(overlaps_from=window_start, overlaps_to=window_end),
        offset=0,
        limit=10,
    )

    assert (listing.total == 1) is expected


async def test_list_is_newest_start_first_and_paginated(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    starts = [SATURDAY + i * 3 * H for i in range(3)]
    for start in starts:
        await _book(db_session, student, server, start)

    page = await reservations.list_reservations(
        db_session, student, reservations.ReservationFilters(), offset=1, limit=1
    )

    assert page.total == 3
    assert [r.starts_at for r in page.items] == [starts[1]]


async def test_list_can_be_soonest_first(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    starts = [SATURDAY + i * 3 * H for i in range(3)]
    for start in reversed(starts):
        await _book(db_session, student, server, start)

    listing = await reservations.list_reservations(
        db_session,
        student,
        reservations.ReservationFilters(order=reservations.ReservationOrder.SOONEST_FIRST),
        offset=0,
        limit=10,
    )

    assert [r.starts_at for r in listing.items] == starts


@pytest.mark.parametrize("order", list(reservations.ReservationOrder))
async def test_equal_starts_page_without_duplicates_or_gaps(
    db_session: AsyncSession, admin: User, order: reservations.ReservationOrder
) -> None:
    """Five reservations share a start, so only the id tiebreak decides their order.
    Paging two at a time must return each of them exactly once."""
    lab = await add_lab(db_session)
    booked = []
    for i in range(5):
        owner = await add_user(db_session, f"tie{i}@example.edu")
        server = await add_server(db_session, lab, f"tie-node-{i}")
        booked.append(await _book(db_session, owner, server))
    # One earlier and one later, so the tied group sits across page boundaries.
    first_owner = await add_user(db_session, "early@example.edu")
    await _book(
        db_session, first_owner, await add_server(db_session, lab, "early"), SATURDAY - 3 * H
    )
    last_owner = await add_user(db_session, "late@example.edu")
    await _book(db_session, last_owner, await add_server(db_session, lab, "late"), SATURDAY + 3 * H)

    seen: list[uuid.UUID] = []
    for offset in range(0, 7, 2):
        page = await reservations.list_reservations(
            db_session,
            admin,
            reservations.ReservationFilters(order=order),
            offset=offset,
            limit=2,
        )
        seen.extend(r.id for r in page.items)

    assert len(seen) == len(set(seen)) == 7
    tied = [rid for rid in seen if rid in {r.id for r in booked}]
    assert tied == sorted(r.id for r in booked)


async def test_list_order_through_the_api(
    api_client: AsyncClient, student_headers: dict[str, str], server: Server
) -> None:
    start = next_saturday(datetime.now(UTC))
    for offset in (3, 0):
        await _post(
            api_client, RESERVATIONS, student_headers, booking_body(server.id, start + offset * H)
        )

    soonest = await api_client.get(
        RESERVATIONS, headers=student_headers, params={"order": "starts_at"}
    )
    default = await api_client.get(RESERVATIONS, headers=student_headers)
    bad = await api_client.get(RESERVATIONS, headers=student_headers, params={"order": "id"})

    starts = [item["starts_at"] for item in soonest.json()["items"]]
    assert starts == sorted(starts)
    assert [item["starts_at"] for item in default.json()["items"]] == starts[::-1]
    assert bad.status_code == 422


async def test_list_filters_through_the_api(
    api_client: AsyncClient, student_headers: dict[str, str], server: Server
) -> None:
    start = next_saturday(datetime.now(UTC))
    await _post(api_client, RESERVATIONS, student_headers, booking_body(server.id, start))

    hit = await api_client.get(
        RESERVATIONS,
        headers=student_headers,
        params={"from": (start + H).isoformat(), "to": (start + 5 * H).isoformat()},
    )
    miss = await api_client.get(
        RESERVATIONS,
        headers=student_headers,
        params={"from": (start + 2 * H).isoformat(), "status": "approved"},
    )
    naive = await api_client.get(
        RESERVATIONS, headers=student_headers, params={"from": "2030-01-12T10:00:00"}
    )

    assert hit.json()["total"] == 1
    assert miss.json()["total"] == 0
    assert naive.status_code == 422


def test_overlap_semantics_are_documented(running_app: FastAPI) -> None:
    parameters = {
        p["name"]: p.get("description", "")
        for p in running_app.openapi()["paths"]["/api/v1/reservations"]["get"]["parameters"]
    }

    assert "ends_at > from" in parameters["from"]
    assert "starts_at < to" in parameters["to"]


class _StatementCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, *_: object, **__: object) -> None:
        self.count += 1


@pytest.fixture
def count_statements(running_app: FastAPI) -> Iterator[_StatementCounter]:
    counter = _StatementCounter()
    sync_engine = running_app.state.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", counter)
    yield counter
    event.remove(sync_engine, "before_cursor_execute", counter)


async def test_listing_a_page_uses_a_fixed_number_of_statements(
    api_client: AsyncClient,
    db_session: AsyncSession,
    student_headers: dict[str, str],
    admin_headers: dict[str, str],
    count_statements: _StatementCounter,
) -> None:
    """Related rows are loaded per relationship, not per reservation: a page of 2 and a
    page of 12 cost the same."""
    lab = await add_lab(db_session)
    counts = []
    for batch in (2, 10):
        for i in range(batch):
            server = await add_server(db_session, lab, f"node-{len(counts)}-{i:02}")
            owner = await add_user(db_session, f"u{len(counts)}-{i}@example.edu")
            await _book(db_session, owner, server)
        count_statements.count = 0
        response = await api_client.get(
            RESERVATIONS, headers=admin_headers, params={"page_size": 50}
        )
        assert response.status_code == 200
        counts.append(count_statements.count)

    assert counts[0] == counts[1]
    # Authenticate, count, page, then server, lab, user, allocations, and GPUs.
    assert counts[1] <= 8


async def test_only_admins_see_whether_a_reservation_is_seeded(
    api_client: AsyncClient,
    db_session: AsyncSession,
    student_headers: dict[str, str],
    admin_headers: dict[str, str],
    restricted: Server,
) -> None:
    start = next_saturday(datetime.now(UTC))
    first = await _post(
        api_client, RESERVATIONS, student_headers, booking_body(restricted.id, start)
    )
    second = await _post(
        api_client, RESERVATIONS, student_headers, booking_body(restricted.id, start + 3 * H)
    )
    third = await _post(
        api_client, RESERVATIONS, student_headers, booking_body(restricted.id, start + 6 * H)
    )
    assert "seeded" not in first
    seeded_id = uuid.UUID(first["id"])
    reservation = await db_session.get(Reservation, seeded_id)
    assert reservation is not None
    reservation.seeded = True
    await db_session.commit()

    # Every response a student gets lacks the field, including their own seeded booking.
    student_list = await api_client.get(RESERVATIONS, headers=student_headers)
    student_one = await api_client.get(f"{RESERVATIONS}/{seeded_id}", headers=student_headers)
    student_cancel = await _post(
        api_client, f"{RESERVATIONS}/{third['id']}/cancel", student_headers
    )
    assert all("seeded" not in item for item in student_list.json()["items"])
    assert "seeded" not in student_one.json()
    assert "seeded" not in student_cancel

    # Admins get it everywhere they read a reservation.
    admin_list = await api_client.get(
        RESERVATIONS, headers=admin_headers, params={"status": "pending"}
    )
    flags = {item["id"]: item["seeded"] for item in admin_list.json()["items"]}
    assert flags == {first["id"]: True, second["id"]: False}
    admin_one = await api_client.get(f"{RESERVATIONS}/{seeded_id}", headers=admin_headers)
    assert admin_one.json()["seeded"] is True
    approved = await _post(api_client, f"{RESERVATIONS}/{seeded_id}/approve", admin_headers)
    rejected = await _post(
        api_client,
        f"{RESERVATIONS}/{second['id']}/reject",
        admin_headers,
        {"reason": "Booked for a class"},
    )
    assert (approved["seeded"], rejected["seeded"]) == (True, False)


def test_the_schema_documents_seeded_for_admins_only(running_app: FastAPI) -> None:
    schemas = running_app.openapi()["components"]["schemas"]

    assert "seeded" in schemas["AdminReservationRead"]["required"]
    assert "seeded" not in schemas["ReservationRead"]["properties"]
