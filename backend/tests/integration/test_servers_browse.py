import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GpuModel, Lab, Server, ServerStatus, UserRole
from app.services import labs, servers
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

SERVERS = "/api/v1/servers"


@dataclass(frozen=True)
class Fleet:
    vision: Lab
    nlp: Lab
    closed: Lab
    by_host: dict[str, Server]


async def _lab(db: AsyncSession, name: str, *, is_active: bool = True) -> Lab:
    return await labs.create_lab(
        db,
        name=name,
        location="Building 1",
        timezone="America/Los_Angeles",
        description=None,
        is_active=is_active,
    )


@pytest.fixture
async def fleet(db_session: AsyncSession) -> Fleet:
    vision = await _lab(db_session, "Vision Lab")
    nlp = await _lab(db_session, "NLP Lab")
    closed = await _lab(db_session, "Closed Lab", is_active=False)
    specs: list[tuple[Lab, str, GpuModel, int, ServerStatus]] = [
        (vision, "rtx4090-node-01", GpuModel.RTX_4090, 4, ServerStatus.AVAILABLE),
        (vision, "a100-node-01", GpuModel.A100_40GB, 8, ServerStatus.AVAILABLE),
        (vision, "l40s-node-01", GpuModel.L40S, 2, ServerStatus.MAINTENANCE),
        (nlp, "h100-node-01", GpuModel.H100_80GB, 8, ServerStatus.AVAILABLE),
        (nlp, "a100-node-02", GpuModel.A100_80GB, 4, ServerStatus.RETIRED),
        (closed, "rtx6000-node-01", GpuModel.RTX_6000_ADA, 2, ServerStatus.AVAILABLE),
    ]
    by_host = {}
    for lab, hostname, model, count, status in specs:
        by_host[hostname] = await servers.create_server(
            db_session,
            lab_id=lab.id,
            hostname=hostname,
            gpu_model=model,
            gpu_count=count,
            cpu_cores=32,
            ram_gb=256,
            status=status,
        )
    await db_session.commit()
    return Fleet(vision=vision, nlp=nlp, closed=closed, by_host=by_host)


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


@pytest.fixture
async def student_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "student@example.edu")
    return bearer(await login(api_client, "student@example.edu"))


def hosts(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [server["hostname"] for server in response.json()["items"]]


async def get(client: AsyncClient, headers: dict[str, str], **params: Any) -> Response:
    return await client.get(SERVERS, params=params, headers=headers)


# --- visibility ---


async def test_admins_see_every_server_ordered_by_lab_and_hostname(
    api_client: AsyncClient, admin_headers: dict[str, str], fleet: Fleet
) -> None:
    response = await get(api_client, admin_headers)

    assert hosts(response) == [
        "rtx6000-node-01",  # Closed Lab
        "a100-node-02",  # NLP Lab
        "h100-node-01",
        "a100-node-01",  # Vision Lab
        "l40s-node-01",
        "rtx4090-node-01",
    ]
    assert response.json()["total"] == 6


async def test_students_do_not_see_retired_servers_or_inactive_labs(
    api_client: AsyncClient, student_headers: dict[str, str], fleet: Fleet
) -> None:
    response = await get(api_client, student_headers)

    assert hosts(response) == ["h100-node-01", "a100-node-01", "l40s-node-01", "rtx4090-node-01"]
    assert response.json()["total"] == 4


async def test_students_cannot_filter_their_way_to_hidden_servers(
    api_client: AsyncClient, student_headers: dict[str, str], fleet: Fleet
) -> None:
    assert hosts(await get(api_client, student_headers, status="retired")) == []
    assert hosts(await get(api_client, student_headers, lab_id=str(fleet.closed.id))) == []


@pytest.mark.parametrize("hostname", ["a100-node-02", "rtx6000-node-01"])
async def test_hidden_server_details_look_missing_to_students(
    api_client: AsyncClient,
    student_headers: dict[str, str],
    admin_headers: dict[str, str],
    fleet: Fleet,
    hostname: str,
) -> None:
    url = f"{SERVERS}/{fleet.by_host[hostname].id}"

    assert_problem(await api_client.get(url, headers=student_headers), 404, "server-not-found")
    assert (await api_client.get(url, headers=admin_headers)).json()["hostname"] == hostname


async def test_server_detail(
    api_client: AsyncClient, student_headers: dict[str, str], fleet: Fleet
) -> None:
    server = fleet.by_host["h100-node-01"]

    response = await api_client.get(f"{SERVERS}/{server.id}", headers=student_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hostname"] == "h100-node-01"
    assert body["gpu_memory_gb"] == 80
    assert body["lab"] == {
        "id": str(fleet.nlp.id),
        "name": "NLP Lab",
        "timezone": "America/Los_Angeles",
        "is_active": True,
    }


async def test_unknown_server(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.get(f"{SERVERS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "server-not-found")


# --- filters ---


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"gpu_model": "A100_40GB"}, ["a100-node-01"]),
        (
            {"min_gpu_memory_gb": 48},
            ["rtx6000-node-01", "a100-node-02", "h100-node-01", "l40s-node-01"],
        ),
        ({"min_gpu_memory_gb": 81}, []),
        ({"min_gpus": 8}, ["h100-node-01", "a100-node-01"]),
        ({"status": "maintenance"}, ["l40s-node-01"]),
        (
            {"min_gpus": 4, "min_gpu_memory_gb": 40},
            ["a100-node-02", "h100-node-01", "a100-node-01"],
        ),
        (
            {"min_gpus": 4, "status": "available"},
            ["h100-node-01", "a100-node-01", "rtx4090-node-01"],
        ),
    ],
)
async def test_filters(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    fleet: Fleet,
    params: dict[str, Any],
    expected: list[str],
) -> None:
    response = await get(api_client, admin_headers, **params)

    assert hosts(response) == expected
    assert response.json()["total"] == len(expected)


async def test_filter_by_lab(
    api_client: AsyncClient, admin_headers: dict[str, str], fleet: Fleet
) -> None:
    response = await get(api_client, admin_headers, lab_id=str(fleet.vision.id), min_gpus=4)

    assert hosts(response) == ["a100-node-01", "rtx4090-node-01"]


async def test_filters_combine_with_student_visibility(
    api_client: AsyncClient, student_headers: dict[str, str], fleet: Fleet
) -> None:
    response = await get(api_client, student_headers, min_gpu_memory_gb=48)

    assert hosts(response) == ["h100-node-01", "l40s-node-01"]


async def test_pagination_reports_the_filtered_total(
    api_client: AsyncClient, admin_headers: dict[str, str], fleet: Fleet
) -> None:
    response = await get(api_client, admin_headers, min_gpus=4, page=2, page_size=2)

    # Four match: a100-node-02, h100-node-01, a100-node-01, rtx4090-node-01.
    assert hosts(response) == ["a100-node-01", "rtx4090-node-01"]
    assert response.json()["total"] == 4


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"gpu_model": "V100"}, "gpu_model"),
        ({"status": "broken"}, "status"),
        ({"min_gpus": 0}, "min_gpus"),
        ({"min_gpus": 17}, "min_gpus"),
        ({"min_gpu_memory_gb": 0}, "min_gpu_memory_gb"),
        ({"lab_id": "not-a-uuid"}, "lab_id"),
    ],
)
async def test_invalid_filters(
    api_client: AsyncClient,
    student_headers: dict[str, str],
    params: dict[str, Any],
    field: str,
) -> None:
    response = await get(api_client, student_headers, **params)

    assert response.status_code == 422, response.text
    assert field in {error["field"] for error in response.json()["errors"]}
