import asyncio
import random
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.session import create_session_factory
from app.models import Gpu, Server, UserRole
from app.models.server import GPU_MEMORY_GB, MAX_GPUS_PER_SERVER
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login
from tests.integration.test_labs import create_lab

pytestmark = pytest.mark.integration

SERVERS = "/api/v1/servers"


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


@pytest.fixture
async def lab_id(api_client: AsyncClient, admin_headers: dict[str, str]) -> str:
    lab: dict[str, Any] = await create_lab(api_client, admin_headers, "Vision Lab")
    return str(lab["id"])


ServerFactory = Callable[..., Any]


@pytest.fixture
def make_server(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str
) -> ServerFactory:
    async def factory(**fields: Any) -> dict[str, Any]:
        body = {
            "lab_id": lab_id,
            "hostname": "a100-node-01",
            "gpu_model": "A100_80GB",
            "gpu_count": 4,
            "cpu_cores": 64,
            "ram_gb": 512,
            **fields,
        }
        response = await api_client.post(SERVERS, json=body, headers=admin_headers)
        assert response.status_code == 201, response.text
        result: dict[str, Any] = response.json()
        return result

    return factory


async def gpu_rows(engine: AsyncEngine, server_id: str) -> list[Gpu]:
    """All GPU rows of a server, read in a fresh session."""
    async with create_session_factory(engine)() as session:
        rows = await session.scalars(
            select(Gpu).where(Gpu.server_id == uuid.UUID(server_id)).order_by(Gpu.index, Gpu.id)
        )
        return list(rows)


async def assert_gpu_invariant(engine: AsyncEngine, server_id: str) -> list[int]:
    """gpu_count equals the active GPU rows, numbered 0..gpu_count-1."""
    async with create_session_factory(engine)() as session:
        count = await session.scalar(
            select(Server.gpu_count).where(Server.id == uuid.UUID(server_id))
        )
    active = [gpu.index for gpu in await gpu_rows(engine, server_id) if gpu.removed_at is None]
    assert active == list(range(count or 0))
    return active


async def patch(
    client: AsyncClient, headers: dict[str, str], server_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.patch(f"{SERVERS}/{server_id}", json=body, headers=headers)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


# --- create ---


async def test_create_server(make_server: ServerFactory, lab_id: str, engine: AsyncEngine) -> None:
    server = await make_server(
        hostname="  A100-Node-01 ", interconnect="NVLink", notes="Rack 4.", requires_approval=True
    )

    assert server["hostname"] == "a100-node-01"
    assert server["lab"]["id"] == lab_id
    assert server["lab"]["name"] == "Vision Lab"
    assert server["lab"]["timezone"] == "America/Los_Angeles"
    assert server["gpu_memory_gb"] == 80
    assert server["status"] == "available"
    assert server["max_booking_hours"] == 12
    assert server["requires_approval"] is True
    assert server["interconnect"] == "NVLink"
    assert await assert_gpu_invariant(engine, server["id"]) == [0, 1, 2, 3]


@pytest.mark.parametrize("model", list(GPU_MEMORY_GB))
async def test_gpu_memory_follows_the_model(make_server: ServerFactory, model: str) -> None:
    server = await make_server(gpu_model=model)

    assert server["gpu_memory_gb"] == GPU_MEMORY_GB[model]  # type: ignore[index]


@pytest.mark.parametrize(
    ("fields", "field"),
    [
        ({"hostname": "node_01"}, "hostname"),
        ({"hostname": "-node"}, "hostname"),
        ({"hostname": "node-"}, "hostname"),
        ({"hostname": "a" * 64}, "hostname"),
        ({"hostname": "node.example.edu"}, "hostname"),
        ({"gpu_model": "V100"}, "gpu_model"),
        ({"gpu_count": 0}, "gpu_count"),
        ({"gpu_count": MAX_GPUS_PER_SERVER + 1}, "gpu_count"),
        ({"cpu_cores": 0}, "cpu_cores"),
        ({"ram_gb": 0}, "ram_gb"),
        ({"max_booking_hours": 0}, "max_booking_hours"),
        ({"max_booking_hours": 337}, "max_booking_hours"),
        ({"status": "broken"}, "status"),
        ({"gpu_memory_gb": 16}, "gpu_memory_gb"),
    ],
)
async def test_create_server_validation(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    lab_id: str,
    fields: dict[str, Any],
    field: str,
) -> None:
    body = {
        "lab_id": lab_id,
        "hostname": "a100-node-01",
        "gpu_model": "A100_80GB",
        "gpu_count": 4,
        "cpu_cores": 64,
        "ram_gb": 512,
        **fields,
    }

    response = await api_client.post(SERVERS, json=body, headers=admin_headers)

    assert response.status_code == 422, response.text
    assert field in {error["field"] for error in response.json()["errors"]}


async def test_create_server_in_unknown_lab(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    body = {
        "lab_id": str(uuid.uuid4()),
        "hostname": "a100-node-01",
        "gpu_model": "A100_80GB",
        "gpu_count": 4,
        "cpu_cores": 64,
        "ram_gb": 512,
    }

    response = await api_client.post(SERVERS, json=body, headers=admin_headers)

    assert_problem(response, 422, "unknown-lab")


async def test_hostnames_are_unique(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory, lab_id: str
) -> None:
    await make_server(hostname="a100-node-01")
    body = {
        "lab_id": lab_id,
        "hostname": "A100-NODE-01",
        "gpu_model": "L40S",
        "gpu_count": 2,
        "cpu_cores": 32,
        "ram_gb": 256,
    }

    response = await api_client.post(SERVERS, json=body, headers=admin_headers)

    assert_problem(response, 409, "hostname-taken")


async def test_concurrent_creates_with_the_same_hostname(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, db_session: AsyncSession
) -> None:
    body = {
        "lab_id": lab_id,
        "hostname": "l40s-node-01",
        "gpu_model": "L40S",
        "gpu_count": 2,
        "cpu_cores": 32,
        "ram_gb": 256,
    }

    responses = await asyncio.gather(
        *(api_client.post(SERVERS, json=body, headers=admin_headers) for _ in range(4))
    )

    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409]
    assert await db_session.scalar(select(func.count()).select_from(Gpu)) == 2


# --- update ---


async def test_update_changes_only_sent_fields(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    server = await make_server(notes="Rack 4.", interconnect="NVLink")

    updated = await patch(
        api_client, admin_headers, server["id"], {"status": "maintenance", "ram_gb": 1024}
    )

    assert updated["status"] == "maintenance"
    assert updated["ram_gb"] == 1024
    assert updated["notes"] == "Rack 4."
    assert updated["interconnect"] == "NVLink"
    assert updated["gpu_count"] == 4


async def test_update_clears_notes_and_interconnect_with_null(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    server = await make_server(notes="Rack 4.", interconnect="NVLink")

    updated = await patch(
        api_client, admin_headers, server["id"], {"notes": None, "interconnect": None}
    )

    assert updated["notes"] is None
    assert updated["interconnect"] is None


async def test_changing_the_model_updates_memory(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    server = await make_server(gpu_model="A100_80GB")

    updated = await patch(api_client, admin_headers, server["id"], {"gpu_model": "RTX_4090"})

    assert updated["gpu_memory_gb"] == 24


async def test_move_server_to_another_lab(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    server = await make_server()
    other = await create_lab(api_client, admin_headers, "NLP Lab")

    updated = await patch(api_client, admin_headers, server["id"], {"lab_id": other["id"]})

    assert updated["lab"]["name"] == "NLP Lab"


async def test_move_server_to_unknown_lab(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    server = await make_server()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"lab_id": str(uuid.uuid4())}, headers=admin_headers
    )

    assert_problem(response, 422, "unknown-lab")


async def test_rename_to_a_taken_hostname(
    api_client: AsyncClient, admin_headers: dict[str, str], make_server: ServerFactory
) -> None:
    await make_server(hostname="a100-node-01")
    other = await make_server(hostname="a100-node-02")

    response = await api_client.patch(
        f"{SERVERS}/{other['id']}", json={"hostname": "a100-node-01"}, headers=admin_headers
    )

    assert_problem(response, 409, "hostname-taken")


async def test_update_unknown_server(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.patch(
        f"{SERVERS}/{uuid.uuid4()}", json={"ram_gb": 64}, headers=admin_headers
    )

    assert_problem(response, 404, "server-not-found")


# --- GPU rows ---


async def test_lowering_gpu_count_soft_removes_the_highest(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    make_server: ServerFactory,
    engine: AsyncEngine,
) -> None:
    server = await make_server(gpu_count=4)
    before = {gpu.index: gpu.id for gpu in await gpu_rows(engine, server["id"])}

    await patch(api_client, admin_headers, server["id"], {"gpu_count": 2})

    rows = await gpu_rows(engine, server["id"])
    assert await assert_gpu_invariant(engine, server["id"]) == [0, 1]
    assert {gpu.index: gpu.id for gpu in rows} == before
    assert [gpu.index for gpu in rows if gpu.removed_at is not None] == [2, 3]


async def test_raising_gpu_count_reactivates_removed_rows_first(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    make_server: ServerFactory,
    engine: AsyncEngine,
) -> None:
    server = await make_server(gpu_count=4)
    original = {gpu.index: gpu.id for gpu in await gpu_rows(engine, server["id"])}
    await patch(api_client, admin_headers, server["id"], {"gpu_count": 1})

    await patch(api_client, admin_headers, server["id"], {"gpu_count": 6})

    rows = await gpu_rows(engine, server["id"])
    assert await assert_gpu_invariant(engine, server["id"]) == [0, 1, 2, 3, 4, 5]
    assert len(rows) == 6
    assert {gpu.index: gpu.id for gpu in rows if gpu.index < 4} == original


async def test_unchanged_gpu_count_leaves_rows_alone(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    make_server: ServerFactory,
    engine: AsyncEngine,
) -> None:
    server = await make_server(gpu_count=3)
    before = [(gpu.id, gpu.removed_at) for gpu in await gpu_rows(engine, server["id"])]

    await patch(api_client, admin_headers, server["id"], {"gpu_count": 3, "ram_gb": 128})

    assert [(gpu.id, gpu.removed_at) for gpu in await gpu_rows(engine, server["id"])] == before


async def test_concurrent_gpu_count_changes_keep_rows_consistent(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    make_server: ServerFactory,
    engine: AsyncEngine,
) -> None:
    """The server row lock applies the changes one at a time. Without it, two requests
    could both read the old count and leave active rows that disagree with gpu_count."""
    server = await make_server(gpu_count=4)
    counts = [random.randint(1, MAX_GPUS_PER_SERVER) for _ in range(12)]  # noqa: S311

    responses = await asyncio.gather(
        *(
            api_client.patch(
                f"{SERVERS}/{server['id']}", json={"gpu_count": count}, headers=admin_headers
            )
            for count in counts
        )
    )

    assert all(response.status_code == 200 for response in responses)
    active = await assert_gpu_invariant(engine, server["id"])
    assert len(active) in counts
    # Reactivation means there is never more than one row per GPU number.
    indexes = [gpu.index for gpu in await gpu_rows(engine, server["id"])]
    assert len(indexes) == len(set(indexes)) == max([4, *counts])


# --- delete ---


async def test_delete_server_removes_its_gpus(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    make_server: ServerFactory,
    engine: AsyncEngine,
) -> None:
    server = await make_server(gpu_count=4)
    await patch(api_client, admin_headers, server["id"], {"gpu_count": 2})

    response = await api_client.delete(f"{SERVERS}/{server['id']}", headers=admin_headers)

    assert response.status_code == 204
    assert await gpu_rows(engine, server["id"]) == []


async def test_delete_unknown_server(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await api_client.delete(f"{SERVERS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "server-not-found")
