import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GpuModel, Lab, Server, UserRole
from app.models.lab import DEFAULT_LAB_TIMEZONE
from app.models.server import GPU_MEMORY_GB
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

LABS = "/api/v1/labs"


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


@pytest.fixture
async def student_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "student@example.edu")
    return bearer(await login(api_client, "student@example.edu"))


async def create_lab(
    client: AsyncClient, headers: dict[str, str], name: str = "Vision Lab", **fields: Any
) -> dict[str, Any]:
    body = {"name": name, "location": "Building 3, Room 210", **fields}
    response = await client.post(LABS, json=body, headers=headers)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def names(response: Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [lab["name"] for lab in response.json()["items"]]


# --- create ---


async def test_create_lab(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    lab = await create_lab(
        api_client,
        admin_headers,
        "  NLP Lab  ",
        timezone="Europe/Berlin",
        description="Language models.",
    )

    assert lab["name"] == "NLP Lab"
    assert lab["timezone"] == "Europe/Berlin"
    assert lab["description"] == "Language models."
    assert lab["is_active"] is True
    assert uuid.UUID(lab["id"])


async def test_create_lab_defaults_timezone(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers)

    assert lab["timezone"] == DEFAULT_LAB_TIMEZONE
    assert lab["description"] is None


@pytest.mark.parametrize("taken", ["Vision Lab", "vision lab", "VISION LAB", "  Vision Lab "])
async def test_lab_names_are_unique_ignoring_case(
    api_client: AsyncClient, admin_headers: dict[str, str], taken: str
) -> None:
    await create_lab(api_client, admin_headers, "Vision Lab")

    response = await api_client.post(
        LABS, json={"name": taken, "location": "Elsewhere"}, headers=admin_headers
    )

    assert_problem(response, 409, "lab-name-taken")


async def test_concurrent_creates_with_the_same_name(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    """The pre-check can pass in both requests; the unique index still decides."""
    responses = await asyncio.gather(
        *(
            api_client.post(
                LABS, json={"name": name, "location": "Building 1"}, headers=admin_headers
            )
            for name in ["Shared Lab", "shared lab", "SHARED LAB", "Shared lab"]
        )
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 409, 409, 409]
    assert await db_session.scalar(select(func.count()).select_from(Lab)) == 1


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "", "location": "B1"}, "name"),
        ({"name": "   ", "location": "B1"}, "name"),
        ({"name": "x" * 101, "location": "B1"}, "name"),
        ({"name": "Lab", "location": ""}, "location"),
        ({"name": "Lab", "location": "B1", "timezone": "Mars/Olympus"}, "timezone"),
        ({"name": "Lab", "location": "B1", "timezone": "PST"}, "timezone"),
        # Offsets that are not whole half hours: +05:45, +08:45, +12:45/+13:45.
        ({"name": "Lab", "location": "B1", "timezone": "Asia/Kathmandu"}, "timezone"),
        ({"name": "Lab", "location": "B1", "timezone": "Australia/Eucla"}, "timezone"),
        ({"name": "Lab", "location": "B1", "timezone": "Pacific/Chatham"}, "timezone"),
        ({"name": "Lab", "location": "B1", "id": str(uuid.uuid4())}, "id"),
    ],
)
async def test_create_lab_validation(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    body: dict[str, Any],
    field: str,
) -> None:
    response = await api_client.post(LABS, json=body, headers=admin_headers)

    assert response.status_code == 422, response.text
    assert field in {error["field"] for error in response.json()["errors"]}


# --- read ---


async def test_list_is_sorted_by_name_ignoring_case(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    for name in ["robotics Lab", "NLP Lab", "Vision Lab"]:
        await create_lab(api_client, admin_headers, name)

    response = await api_client.get(LABS, headers=admin_headers)

    assert names(response) == ["NLP Lab", "robotics Lab", "Vision Lab"]
    assert response.json()["total"] == 3


async def test_list_paginates(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    for name in ["A Lab", "B Lab", "C Lab"]:
        await create_lab(api_client, admin_headers, name)

    response = await api_client.get(LABS, params={"page": 2, "page_size": 2}, headers=admin_headers)

    assert names(response) == ["C Lab"]
    assert response.json() | {"items": None} == {
        "items": None,
        "total": 3,
        "page": 2,
        "page_size": 2,
    }


async def test_students_only_see_active_labs(
    api_client: AsyncClient, admin_headers: dict[str, str], student_headers: dict[str, str]
) -> None:
    await create_lab(api_client, admin_headers, "Open Lab")
    closed = await create_lab(api_client, admin_headers, "Closed Lab", is_active=False)

    assert names(await api_client.get(LABS, headers=student_headers)) == ["Open Lab"]
    assert (
        names(await api_client.get(LABS, params={"is_active": "false"}, headers=student_headers))
        == []
    )
    response = await api_client.get(f"{LABS}/{closed['id']}", headers=student_headers)
    assert_problem(response, 404, "lab-not-found")


async def test_admins_see_and_filter_inactive_labs(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    await create_lab(api_client, admin_headers, "Open Lab")
    closed = await create_lab(api_client, admin_headers, "Closed Lab", is_active=False)

    assert names(await api_client.get(LABS, headers=admin_headers)) == ["Closed Lab", "Open Lab"]
    assert names(
        await api_client.get(LABS, params={"is_active": "false"}, headers=admin_headers)
    ) == ["Closed Lab"]
    response = await api_client.get(f"{LABS}/{closed['id']}", headers=admin_headers)
    assert response.json()["name"] == "Closed Lab"


async def test_get_unknown_lab(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.get(f"{LABS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "lab-not-found")


# --- update ---


async def test_update_changes_only_sent_fields(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers, description="Cameras and GPUs.")

    response = await api_client.patch(
        f"{LABS}/{lab['id']}",
        json={"location": "Building 9", "timezone": "America/New_York"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["location"] == "Building 9"
    assert updated["timezone"] == "America/New_York"
    assert updated["name"] == "Vision Lab"
    assert updated["description"] == "Cameras and GPUs."


@pytest.mark.parametrize(
    "timezone",
    [
        "Asia/Kolkata",  # +05:30
        "Australia/Adelaide",  # +09:30 / +10:30
        "Australia/Lord_Howe",  # +10:30 / +11:00, a 30-minute daylight saving shift
        "UTC",
    ],
)
async def test_half_hour_offsets_are_accepted(
    api_client: AsyncClient, admin_headers: dict[str, str], timezone: str
) -> None:
    response = await api_client.post(
        LABS, json={"name": "Lab", "location": "B1", "timezone": timezone}, headers=admin_headers
    )

    assert response.status_code == 201, response.text


async def test_update_rejects_timezone_off_the_half_hour_grid(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers)

    response = await api_client.patch(
        f"{LABS}/{lab['id']}", json={"timezone": "Asia/Kathmandu"}, headers=admin_headers
    )

    assert response.status_code == 422, response.text
    assert "timezone" in {error["field"] for error in response.json()["errors"]}


async def test_update_clears_description_with_null(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers, description="Old text.")

    response = await api_client.patch(
        f"{LABS}/{lab['id']}", json={"description": None}, headers=admin_headers
    )

    assert response.json()["description"] is None


async def test_rename_to_a_different_case_of_its_own_name(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers, "Vision lab")

    response = await api_client.patch(
        f"{LABS}/{lab['id']}", json={"name": "Vision Lab"}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Vision Lab"


async def test_rename_to_another_labs_name(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    await create_lab(api_client, admin_headers, "Vision Lab")
    other = await create_lab(api_client, admin_headers, "NLP Lab")

    response = await api_client.patch(
        f"{LABS}/{other['id']}", json={"name": "VISION lab"}, headers=admin_headers
    )

    assert_problem(response, 409, "lab-name-taken")


async def test_deactivate_and_reactivate(
    api_client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    lab = await create_lab(api_client, admin_headers)
    url = f"{LABS}/{lab['id']}"

    off = await api_client.patch(url, json={"is_active": False}, headers=admin_headers)
    on = await api_client.patch(url, json={"is_active": True}, headers=admin_headers)

    assert off.json()["is_active"] is False
    assert on.json()["is_active"] is True


async def test_update_unknown_lab(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.patch(
        f"{LABS}/{uuid.uuid4()}", json={"location": "B1"}, headers=admin_headers
    )

    assert_problem(response, 404, "lab-not-found")


# --- delete ---


async def test_delete_empty_lab(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    lab = await create_lab(api_client, admin_headers)

    response = await api_client.delete(f"{LABS}/{lab['id']}", headers=admin_headers)

    assert response.status_code == 204
    assert (await api_client.get(f"{LABS}/{lab['id']}", headers=admin_headers)).status_code == 404


async def test_delete_lab_with_servers_is_a_conflict(
    api_client: AsyncClient, admin_headers: dict[str, str], db_session: AsyncSession
) -> None:
    lab = await create_lab(api_client, admin_headers)
    db_session.add(
        Server(
            lab_id=uuid.UUID(lab["id"]),
            hostname="a100-node-01",
            gpu_model=GpuModel.A100_80GB,
            gpu_memory_gb=GPU_MEMORY_GB[GpuModel.A100_80GB],
            gpu_count=4,
            cpu_cores=64,
            ram_gb=512,
        )
    )
    await db_session.commit()

    response = await api_client.delete(f"{LABS}/{lab['id']}", headers=admin_headers)

    assert_problem(response, 409, "lab-not-empty")
    assert (await api_client.get(f"{LABS}/{lab['id']}", headers=admin_headers)).status_code == 200


async def test_delete_unknown_lab(api_client: AsyncClient, admin_headers: dict[str, str]) -> None:
    response = await api_client.delete(f"{LABS}/{uuid.uuid4()}", headers=admin_headers)

    assert_problem(response, 404, "lab-not-found")
