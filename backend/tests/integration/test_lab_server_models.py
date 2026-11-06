from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Gpu, GpuModel, Lab, Server, ServerStatus
from app.models.lab import DEFAULT_LAB_TIMEZONE
from app.models.server import DEFAULT_MAX_BOOKING_HOURS, GPU_MEMORY_GB, MAX_GPUS_PER_SERVER

pytestmark = pytest.mark.integration


def _lab(name: str = "Vision Lab", **overrides: object) -> Lab:
    values: dict[str, object] = {"name": name, "location": "Building 3, Room 210"}
    values.update(overrides)
    return Lab(**values)


def _server(lab: Lab, hostname: str = "a100-node-01", **overrides: object) -> Server:
    values: dict[str, object] = {
        "lab_id": lab.id,
        "hostname": hostname,
        "gpu_model": GpuModel.A100_80GB,
        "gpu_memory_gb": GPU_MEMORY_GB[GpuModel.A100_80GB],
        "gpu_count": 4,
        "cpu_cores": 64,
        "ram_gb": 512,
    }
    values.update(overrides)
    return Server(**values)


async def _saved_lab(db: AsyncSession, name: str = "Vision Lab") -> Lab:
    lab = _lab(name)
    db.add(lab)
    await db.flush()
    return lab


async def test_lab_defaults(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    await db_session.commit()
    await db_session.refresh(lab)

    assert lab.timezone == DEFAULT_LAB_TIMEZONE
    assert lab.is_active is True
    assert lab.description is None


async def test_lab_names_are_unique_ignoring_case(db_session: AsyncSession) -> None:
    await _saved_lab(db_session, "Vision Lab")
    await db_session.commit()

    db_session.add(_lab("vision LAB"))
    with pytest.raises(IntegrityError, match="uq_labs_name_lower"):
        await db_session.commit()


async def test_lab_name_keeps_its_case(db_session: AsyncSession) -> None:
    await _saved_lab(db_session, "NLP Lab")
    await db_session.commit()

    assert await db_session.scalar(text("SELECT name FROM labs")) == "NLP Lab"


async def test_server_defaults_and_enum_values(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    server = _server(lab)
    db_session.add(server)
    await db_session.commit()
    await db_session.refresh(server)

    assert server.status is ServerStatus.AVAILABLE
    assert server.requires_approval is False
    assert server.max_booking_hours == DEFAULT_MAX_BOOKING_HOURS
    row = (
        await db_session.execute(text("SELECT gpu_model::text, status::text FROM servers"))
    ).one()
    assert tuple(row) == ("A100_80GB", "available")


async def test_hostname_is_unique(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    db_session.add(_server(lab))
    await db_session.commit()

    db_session.add(_server(lab))
    with pytest.raises(IntegrityError, match="uq_servers_hostname"):
        await db_session.commit()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"gpu_count": 0}, "ck_servers_gpu_count_range"),
        ({"gpu_count": MAX_GPUS_PER_SERVER + 1}, "ck_servers_gpu_count_range"),
        ({"gpu_memory_gb": 0}, "ck_servers_gpu_memory_positive"),
        ({"cpu_cores": 0}, "ck_servers_cpu_cores_positive"),
        ({"ram_gb": 0}, "ck_servers_ram_positive"),
        ({"max_booking_hours": 0}, "ck_servers_max_booking_hours_range"),
        ({"max_booking_hours": 337}, "ck_servers_max_booking_hours_range"),
        ({"hostname": "A100-Node-01"}, "ck_servers_hostname_lowercase"),
    ],
)
async def test_server_checks(
    db_session: AsyncSession, overrides: dict[str, object], constraint: str
) -> None:
    lab = await _saved_lab(db_session)
    db_session.add(_server(lab, **overrides))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.commit()


async def test_lab_with_servers_cannot_be_deleted(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    db_session.add(_server(lab))
    await db_session.commit()

    with pytest.raises(IntegrityError, match="fk_servers_lab_id_labs"):
        await db_session.execute(text("DELETE FROM labs"))


async def test_deleting_server_deletes_its_gpus(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    server = _server(lab, gpu_count=2)
    db_session.add(server)
    await db_session.flush()
    db_session.add_all([Gpu(server_id=server.id, index=i) for i in range(2)])
    await db_session.commit()

    await db_session.execute(text("DELETE FROM servers"))
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Gpu)) == 0


async def test_only_one_active_gpu_per_index(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    server = _server(lab, gpu_count=1)
    db_session.add(server)
    await db_session.flush()
    db_session.add(Gpu(server_id=server.id, index=0))
    await db_session.commit()

    db_session.add(Gpu(server_id=server.id, index=0))
    with pytest.raises(IntegrityError, match="uq_gpus_server_id_index_active"):
        await db_session.commit()


async def test_removed_gpus_do_not_block_their_index(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    server = _server(lab, gpu_count=1)
    db_session.add(server)
    await db_session.flush()
    db_session.add_all(
        [
            Gpu(server_id=server.id, index=0, removed_at=datetime.now(UTC)),
            Gpu(server_id=server.id, index=0),
        ]
    )
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Gpu)) == 2


async def test_gpu_index_cannot_be_negative(db_session: AsyncSession) -> None:
    lab = await _saved_lab(db_session)
    server = _server(lab)
    db_session.add(server)
    await db_session.flush()
    db_session.add(Gpu(server_id=server.id, index=-1))
    with pytest.raises(IntegrityError, match="ck_gpus_index_non_negative"):
        await db_session.commit()


async def test_foreign_keys_have_plain_indexes(db_session: AsyncSession) -> None:
    """The partial unique index on gpus skips removed rows, so cascades and history
    lookups by server need a plain index on gpus.server_id."""
    rows = await db_session.execute(
        text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE indexname IN ('ix_gpus_server_id', 'ix_servers_lab_id')"
        )
    )
    definitions = {row.indexname: row.indexdef for row in rows}

    assert set(definitions) == {"ix_gpus_server_id", "ix_servers_lab_id"}
    assert all("WHERE" not in definition for definition in definitions.values())
