"""What reservations block: removing GPUs, retiring or deleting a server, deleting a
user, and what deactivating a user does to their reservations."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    GpusInUse,
    InvalidToken,
    ServerHasReservations,
    UserHasReservations,
)
from app.models import (
    AuditLog,
    Gpu,
    GpuAllocation,
    Reservation,
    ReservationStatus,
    Server,
    ServerStatus,
    User,
    UserRole,
)
from app.services import reservations, servers, users
from app.services.reservations import NewReservation, create_reservation
from tests.integration.reservation_helpers import add_lab, add_server, add_user, next_saturday
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

H = timedelta(hours=1)
# Monday 7 January 2030, 10:00 in Los Angeles.
NOW = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
# Saturday 12 January 2030, 08:00 in Los Angeles.
SATURDAY = datetime(2030, 1, 12, 16, 0, tzinfo=UTC)


def _request(server: Server, start: datetime = SATURDAY, *, gpus: int = 1) -> NewReservation:
    return NewReservation(
        server_id=server.id, gpu_count=gpus, starts_at=start, ends_at=start + 2 * H, purpose="Run"
    )


async def _book(
    db: AsyncSession, user: User, server: Server, start: datetime = SATURDAY, *, gpus: int = 1
) -> Reservation:
    reservation = await create_reservation(db, user, _request(server, start, gpus=gpus), now=NOW)
    await db.commit()
    return reservation


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await add_user(db_session, "student@example.edu")


@pytest.fixture
async def admin(db_session: AsyncSession) -> User:
    return await add_user(db_session, "admin@example.edu", role=UserRole.ADMIN)


@pytest.fixture
async def server(db_session: AsyncSession) -> Server:
    return await add_server(db_session, await add_lab(db_session), gpu_count=4)


async def _reload(db: AsyncSession, reservation: Reservation) -> Reservation:
    return await reservations.load_reservation(db, reservation.id)


# --- removing GPUs ---


async def test_reducing_gpus_held_by_an_upcoming_reservation_is_refused(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, gpus=3)  # GPUs 0-2

    with pytest.raises(GpusInUse, match="GPUs 2 are held by 1 current or upcoming reservation"):
        await servers.update_server(db_session, server.id, gpu_count=2, now=NOW)


async def test_pending_reservations_count_as_in_use(
    db_session: AsyncSession, student: User
) -> None:
    lab = await add_lab(db_session)
    restricted = await add_server(db_session, lab, "h100-node-01", requires_approval=True)
    pending = await _book(db_session, student, restricted, gpus=4)
    assert pending.status is ReservationStatus.PENDING

    with pytest.raises(GpusInUse):
        await servers.update_server(db_session, restricted.id, gpu_count=1, now=NOW)


async def test_running_reservations_count_as_in_use(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, gpus=4)

    with pytest.raises(GpusInUse):
        await servers.update_server(
            db_session,
            server.id,
            gpu_count=1,
            now=SATURDAY + H,  # mid-booking
        )


async def test_reducing_is_allowed_when_removed_gpus_are_free(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, gpus=2)  # GPUs 0-1 stay

    updated = await servers.update_server(db_session, server.id, gpu_count=2, now=NOW)

    assert updated.gpu_count == 2


async def test_finished_and_cancelled_reservations_do_not_block(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    finished = await _book(db_session, student, server, gpus=4)
    cancelled = await _book(db_session, student, server, SATURDAY + 4 * H, gpus=4)
    await reservations.finish_reservation(db_session, cancelled, now=NOW, actor_id=admin.id)
    await db_session.commit()

    # After the first booking has ended.
    updated = await servers.update_server(
        db_session, server.id, gpu_count=1, now=finished.ends_at + timedelta(minutes=1)
    )

    assert updated.gpu_count == 1


async def test_gpus_in_use_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession, student: User, server: Server
) -> None:
    await create_reservation(db_session, student, _future_request(server), now=datetime.now(UTC))
    await db_session.commit()
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "boss@example.edu"))

    response = await api_client.patch(
        f"/api/v1/servers/{server.id}", json={"gpu_count": 1}, headers=headers
    )

    assert_problem(response, 409, "gpus-in-use")


def _future_request(server: Server) -> NewReservation:
    """Four GPUs next Saturday, for tests that go through the API and its real clock."""
    start = next_saturday(datetime.now(UTC))
    return NewReservation(
        server_id=server.id, gpu_count=4, starts_at=start, ends_at=start + H, purpose="Run"
    )


# --- retiring ---


async def test_retiring_with_upcoming_reservations_is_refused(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server)

    with pytest.raises(GpusInUse):
        await servers.update_server(db_session, server.id, status=ServerStatus.RETIRED, now=NOW)


async def test_retiring_after_reservations_end_is_allowed(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    updated = await servers.update_server(
        db_session, server.id, status=ServerStatus.RETIRED, now=booked.ends_at
    )

    assert updated.status is ServerStatus.RETIRED


async def test_maintenance_leaves_reservations_in_place(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)

    await servers.update_server(db_session, server.id, status=ServerStatus.MAINTENANCE, now=NOW)
    await db_session.commit()

    assert (await _reload(db_session, booked)).status is ReservationStatus.APPROVED


# --- deleting a server ---


async def test_server_with_any_reservation_cannot_be_deleted(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)
    await reservations.finish_reservation(db_session, booked, now=NOW, actor_id=admin.id)
    await db_session.commit()

    with pytest.raises(ServerHasReservations, match="Retire it instead"):
        await servers.delete_server(db_session, server.id)


async def test_raced_server_delete_is_caught_by_the_foreign_key(
    db_session: AsyncSession, student: User, server: Server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a reservation slipped past the check, the RESTRICT foreign key (SQLSTATE
    23503) still refuses the delete with the same error."""
    await _book(db_session, student, server)

    async def skip(*_: object) -> None:
        return None

    monkeypatch.setattr(servers, "_ensure_server_deletable", skip)

    server_id = server.id
    with pytest.raises(ServerHasReservations):
        await servers.delete_server(db_session, server_id)
    await db_session.rollback()
    assert await db_session.get(Server, server_id) is not None


async def test_server_delete_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server)
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "boss@example.edu"))

    response = await api_client.delete(f"/api/v1/servers/{server.id}", headers=headers)

    assert_problem(response, 409, "server-has-reservations")


# --- deleting a user ---


async def test_user_with_reservations_cannot_be_deleted(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    await _book(db_session, student, server)

    with pytest.raises(UserHasReservations, match="Deactivate it instead"):
        await users.delete_user(db_session, admin, student.id)


async def test_raced_user_delete_is_caught_by_the_foreign_key(
    db_session: AsyncSession,
    student: User,
    admin: User,
    server: Server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _book(db_session, student, server)

    async def skip(*_: object) -> None:
        return None

    monkeypatch.setattr(users, "_ensure_user_deletable", skip)

    with pytest.raises(UserHasReservations):
        await users.delete_user(db_session, admin, student.id)


async def test_user_delete_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession, server: Server
) -> None:
    target = await create_user(db_session, "target@example.edu")
    await _book(db_session, target, server)
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "boss@example.edu"))

    response = await api_client.delete(f"/api/v1/users/{target.id}", headers=headers)

    assert_problem(response, 409, "user-has-reservations")


# --- deactivating a user ---


async def test_deactivation_releases_held_reservations(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    restricted = await add_server(
        db_session, await add_lab(db_session, "NLP Lab"), "h100-node-01", requires_approval=True
    )
    running = await _book(db_session, student, server, SATURDAY)  # 16:00-18:00 UTC
    upcoming = await _book(db_session, student, server, SATURDAY + 4 * H)
    pending = await _book(db_session, student, restricted, SATURDAY + 4 * H)
    ended = await _book(db_session, student, restricted, SATURDAY - 4 * H)
    # Approve the restricted one that has already run, so it is an approved past booking.
    ended.status = ReservationStatus.APPROVED
    await db_session.commit()

    deactivated_at = SATURDAY + H + timedelta(minutes=10)  # running is mid-way
    await users.update_user(db_session, admin, student.id, is_active=False, now=deactivated_at)
    await db_session.commit()

    running = await _reload(db_session, running)
    assert running.status is ReservationStatus.COMPLETED
    assert running.ends_at == SATURDAY + H + timedelta(minutes=30)
    assert (await _reload(db_session, upcoming)).status is ReservationStatus.CANCELLED
    assert (await _reload(db_session, pending)).status is ReservationStatus.CANCELLED
    # A booking that already ended is left for the completion job.
    assert (await _reload(db_session, ended)).status is ReservationStatus.APPROVED

    released = {running.id, upcoming.id, pending.id}
    allocations = list(
        await db_session.scalars(
            select(GpuAllocation).where(GpuAllocation.reservation_id.in_(released))
        )
    )
    assert allocations and not any(a.active for a in allocations)
    assert all(a.ends_at == running.ends_at for a in allocations if a.reservation_id == running.id)

    entries = list(
        await db_session.scalars(select(AuditLog).where(AuditLog.action != "reservation.created"))
    )
    assert {e.entity_id for e in entries} == released
    assert all(e.actor_id == admin.id for e in entries)
    assert all(e.payload["reason"] == "user_deactivated" for e in entries)
    assert {e.action for e in entries} == {"reservation.cancelled", "reservation.ended_early"}


async def test_deactivation_frees_gpus_for_others(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    await _book(db_session, student, server, gpus=4)
    await users.update_user(db_session, admin, student.id, is_active=False, now=NOW)
    await db_session.commit()

    other = await add_user(db_session, "other@example.edu")
    rebooked = await _book(db_session, other, server, gpus=4)

    assert sorted(a.gpu.index for a in rebooked.allocations) == [0, 1, 2, 3]


async def test_reactivation_leaves_reservations_alone(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    booked = await _book(db_session, student, server)
    await users.update_user(db_session, admin, student.id, is_active=False, now=NOW)
    await db_session.commit()

    await users.update_user(db_session, admin, student.id, is_active=True, now=NOW)
    await db_session.commit()

    assert (await _reload(db_session, booked)).status is ReservationStatus.CANCELLED


async def test_deactivation_through_the_api(
    api_client: AsyncClient, db_session: AsyncSession, server: Server
) -> None:
    target = await create_user(db_session, "target@example.edu")
    request = _future_request(server)
    booked = await create_reservation(db_session, target, request, now=datetime.now(UTC))
    await db_session.commit()
    await create_user(db_session, "boss@example.edu", UserRole.ADMIN)
    headers = bearer(await login(api_client, "boss@example.edu"))

    response = await api_client.patch(
        f"/api/v1/users/{target.id}", json={"is_active": False}, headers=headers
    )

    assert response.status_code == 200, response.text
    status = await db_session.scalar(
        select(Reservation.status)
        .where(Reservation.id == booked.id)
        .execution_options(populate_existing=True)
    )
    assert status is ReservationStatus.CANCELLED


async def test_inactive_account_cannot_book(
    db_session: AsyncSession, student: User, admin: User, server: Server
) -> None:
    """The User object the request loaded is stale: the booking re-reads the account
    after taking the user lock."""
    stale = await db_session.get(User, student.id)
    assert stale is not None
    await users.update_user(db_session, admin, student.id, is_active=False, now=NOW)
    await db_session.commit()

    with pytest.raises(InvalidToken, match="not active"):
        await create_reservation(db_session, stale, _request(server), now=NOW)


async def test_gpu_rows_unchanged_after_refused_reduction(
    db_session: AsyncSession, student: User, server: Server
) -> None:
    await _book(db_session, student, server, gpus=4)

    server_id = server.id
    with pytest.raises(GpusInUse):
        await servers.update_server(db_session, server_id, gpu_count=1, now=NOW)
    await db_session.rollback()

    active = await db_session.scalars(
        select(Gpu.index).where(Gpu.server_id == server_id, Gpu.removed_at.is_(None))
    )
    assert sorted(active) == [0, 1, 2, 3]
