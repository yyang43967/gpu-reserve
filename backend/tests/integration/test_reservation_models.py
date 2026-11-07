"""Database-level guarantees of the reservation tables, tested without the services."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Gpu,
    GpuAllocation,
    GpuModel,
    Lab,
    Reservation,
    ReservationStatus,
    Server,
    User,
)
from app.models.reservation import ALLOCATION_OVERLAP_CONSTRAINT
from app.models.server import GPU_MEMORY_GB

pytestmark = pytest.mark.integration

T0 = datetime(2030, 1, 7, 18, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


async def _world(db: AsyncSession) -> tuple[User, Server, list[Gpu]]:
    user = User(email="student@example.edu", password_hash="x", full_name="Student")
    lab = Lab(name="Vision Lab", location="Building 3")
    db.add_all([user, lab])
    await db.flush()
    server = Server(
        lab_id=lab.id,
        hostname="a100-node-01",
        gpu_model=GpuModel.A100_80GB,
        gpu_memory_gb=GPU_MEMORY_GB[GpuModel.A100_80GB],
        gpu_count=2,
        cpu_cores=64,
        ram_gb=512,
    )
    db.add(server)
    await db.flush()
    gpus = [Gpu(server_id=server.id, index=index) for index in range(2)]
    db.add_all(gpus)
    await db.commit()
    return user, server, gpus


async def _book(
    db: AsyncSession,
    user: User,
    server: Server,
    gpu: Gpu,
    start: datetime,
    end: datetime,
    *,
    active: bool = True,
) -> Reservation:
    reservation = Reservation(
        user_id=user.id,
        server_id=server.id,
        gpu_count=1,
        starts_at=start,
        ends_at=end,
        status=ReservationStatus.APPROVED if active else ReservationStatus.CANCELLED,
        purpose="Training",
    )
    db.add(reservation)
    await db.flush()
    db.add(
        GpuAllocation(
            reservation_id=reservation.id,
            gpu_id=gpu.id,
            starts_at=start,
            ends_at=end,
            active=active,
        )
    )
    await db.commit()
    return reservation


async def test_overlapping_active_allocations_are_rejected(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR)

    with pytest.raises(IntegrityError, match=ALLOCATION_OVERLAP_CONSTRAINT) as caught:
        await _book(db_session, user, server, gpus[0], T0 + HOUR, T0 + 3 * HOUR)
    assert caught.value.orig.sqlstate == "23P01"  # type: ignore[union-attr]


async def test_adjacent_ranges_do_not_overlap(db_session: AsyncSession) -> None:
    """Ranges are half-open, so one booking may start exactly when another ends."""
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)
    await _book(db_session, user, server, gpus[0], T0 + HOUR, T0 + 2 * HOUR)


async def test_inactive_allocations_do_not_block(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR, active=False)
    await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR)


async def test_different_gpus_may_overlap(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR)
    await _book(db_session, user, server, gpus[1], T0, T0 + 2 * HOUR)


async def test_reactivating_an_overlapping_allocation_is_rejected(
    db_session: AsyncSession,
) -> None:
    user, server, gpus = await _world(db_session)
    cancelled = await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR, active=False)
    await _book(db_session, user, server, gpus[0], T0, T0 + 2 * HOUR)

    with pytest.raises(IntegrityError, match=ALLOCATION_OVERLAP_CONSTRAINT):
        await db_session.execute(
            text("UPDATE gpu_allocations SET active = true WHERE reservation_id = :id"),
            {"id": cancelled.id},
        )


async def test_allocation_active_cannot_be_null(db_session: AsyncSession) -> None:
    """WHERE (active) skips NULL rows, so a NULL would escape the exclusion constraint."""
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)

    with pytest.raises(IntegrityError, match="not-null"):
        await db_session.execute(text("UPDATE gpu_allocations SET active = NULL"))


async def test_allocation_active_defaults_to_true(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    reservation = await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)

    await db_session.execute(
        text(
            "INSERT INTO gpu_allocations (reservation_id, gpu_id, starts_at, ends_at) "
            "VALUES (:reservation, :gpu, :start, :end)"
        ),
        {"reservation": reservation.id, "gpu": gpus[1].id, "start": T0, "end": T0 + HOUR},
    )

    assert await db_session.scalar(
        text("SELECT active FROM gpu_allocations WHERE gpu_id = :gpu"), {"gpu": gpus[1].id}
    )


@pytest.mark.parametrize(
    ("table", "column_sql", "constraint"),
    [
        ("reservations", "ends_at = starts_at", "ck_reservations_ends_after_start"),
        ("reservations", "gpu_count = 0", "ck_reservations_gpu_count_positive"),
        ("gpu_allocations", "ends_at = starts_at", "ck_gpu_allocations_ends_after_start"),
    ],
)
async def test_checks(
    db_session: AsyncSession, table: str, column_sql: str, constraint: str
) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)

    with pytest.raises(IntegrityError, match=constraint):
        await db_session.execute(text(f"UPDATE {table} SET {column_sql}"))  # noqa: S608


async def test_users_and_servers_with_reservations_cannot_be_deleted(
    db_session: AsyncSession,
) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)

    for statement, constraint in [
        ("DELETE FROM users", "fk_reservations_user_id_users"),
        ("DELETE FROM servers", "fk_reservations_server_id_servers"),
        ("DELETE FROM gpus", "fk_gpu_allocations_gpu_id_gpus"),
    ]:
        with pytest.raises(IntegrityError, match=constraint):
            await db_session.execute(text(statement))
        await db_session.rollback()


async def test_deciding_admin_can_be_deleted(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    reservation = await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)
    admin = User(email="admin@example.edu", password_hash="x", full_name="Admin")
    db_session.add(admin)
    await db_session.flush()
    reservation.decided_by = admin.id
    await db_session.commit()

    await db_session.delete(admin)
    await db_session.commit()

    assert await db_session.scalar(select(Reservation.decided_by)) is None


async def test_allocations_go_with_their_reservation(db_session: AsyncSession) -> None:
    user, server, gpus = await _world(db_session)
    await _book(db_session, user, server, gpus[0], T0, T0 + HOUR)

    await db_session.execute(text("DELETE FROM reservations"))
    await db_session.commit()

    assert await db_session.scalar(select(GpuAllocation.id)) is None
