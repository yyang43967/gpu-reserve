import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserRole
from tests.fakes import FakeStorage
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login
from tests.integration.test_labs import create_lab

pytestmark = pytest.mark.integration

SERVERS = "/api/v1/servers"
MIB = 1024 * 1024


@pytest.fixture
def storage(running_app: FastAPI) -> FakeStorage:
    fake = FakeStorage()
    running_app.state.storage = fake
    return fake


@pytest.fixture
async def admin_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    return bearer(await login(api_client, "admin@example.edu"))


@pytest.fixture
async def student_headers(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, str]:
    await create_user(db_session, "student@example.edu")
    return bearer(await login(api_client, "student@example.edu"))


@pytest.fixture
async def lab_id(api_client: AsyncClient, admin_headers: dict[str, str]) -> str:
    return str((await create_lab(api_client, admin_headers))["id"])


def uploaded(storage: FakeStorage, size: int = 1000, content_type: str = "image/png") -> str:
    key = f"servers/{uuid.uuid4().hex}.png"
    storage.put(key, size, content_type)
    return key


async def create(client: AsyncClient, headers: dict[str, str], lab_id: str, **fields: Any) -> Any:
    body = {
        "lab_id": lab_id,
        "hostname": "a100-node-01",
        "gpu_model": "A100_80GB",
        "gpu_count": 4,
        "cpu_cores": 64,
        "ram_gb": 512,
        **fields,
    }
    return await client.post(SERVERS, json=body, headers=headers)


async def test_create_with_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage)

    response = await create(api_client, admin_headers, lab_id, image_key=key)

    assert response.status_code == 201, response.text
    assert response.json()["image_url"].startswith(f"http://storage.test/uploads/{key}?")
    assert "image_key" not in response.json()


async def test_create_without_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    response = await create(api_client, admin_headers, lab_id)

    assert response.json()["image_url"] is None


@pytest.mark.parametrize(
    ("make_key", "slug"),
    [
        (lambda storage: "servers/never-uploaded.png", "upload-not-found"),
        (lambda storage: f"avatars/{uuid.uuid4()}/a.png", "invalid-upload"),
        (lambda storage: uploaded(storage, size=5 * MIB + 1), "invalid-upload"),
        (lambda storage: uploaded(storage, content_type="text/html"), "invalid-upload"),
    ],
)
async def test_create_rejects_bad_images_and_saves_nothing(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    lab_id: str,
    storage: FakeStorage,
    make_key: Any,
    slug: str,
) -> None:
    response = await create(api_client, admin_headers, lab_id, image_key=make_key(storage))

    assert_problem(response, 422, slug)
    listing = await api_client.get(SERVERS, headers=admin_headers)
    assert listing.json()["total"] == 0


async def test_oversized_upload_is_deleted(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage, size=5 * MIB + 1)

    await create(api_client, admin_headers, lab_id, image_key=key)

    assert storage.deleted == [key]


async def test_replacing_the_image_deletes_the_old_one_after_saving(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    old, new = uploaded(storage), uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=old)).json()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"image_key": new}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    assert new in response.json()["image_url"]
    assert storage.deleted == [old]
    assert new in storage.objects


async def test_same_key_deletes_nothing(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=key)).json()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"image_key": key, "ram_gb": 256}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    assert storage.deleted == []
    assert key in response.json()["image_url"]


async def test_null_removes_the_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=key)).json()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"image_key": None}, headers=admin_headers
    )

    assert response.json()["image_url"] is None
    assert storage.deleted == [key]


async def test_omitting_image_key_keeps_the_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=key)).json()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"ram_gb": 256}, headers=admin_headers
    )

    assert key in response.json()["image_url"]
    assert storage.deleted == []


async def test_failed_update_keeps_the_old_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    """The new image is valid, but another field fails, so nothing is saved and the
    old object must survive."""
    old, new = uploaded(storage), uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=old)).json()
    await create(api_client, admin_headers, lab_id, hostname="a100-node-02")

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}",
        json={"hostname": "a100-node-02", "image_key": new},
        headers=admin_headers,
    )

    assert_problem(response, 409, "hostname-taken")
    assert storage.deleted == []
    current = await api_client.get(f"{SERVERS}/{server['id']}", headers=admin_headers)
    assert old in current.json()["image_url"]


async def test_failed_image_check_keeps_other_changes_out(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    old = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=old)).json()

    response = await api_client.patch(
        f"{SERVERS}/{server['id']}",
        json={"ram_gb": 256, "image_key": "servers/never-uploaded.png"},
        headers=admin_headers,
    )

    assert_problem(response, 422, "upload-not-found")
    current = (await api_client.get(f"{SERVERS}/{server['id']}", headers=admin_headers)).json()
    assert current["ram_gb"] == 512
    assert storage.deleted == []


async def test_deleting_a_server_deletes_its_image(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    key = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=key)).json()

    response = await api_client.delete(f"{SERVERS}/{server['id']}", headers=admin_headers)

    assert response.status_code == 204
    assert storage.deleted == [key]


async def test_a_failing_delete_does_not_fail_the_request(
    api_client: AsyncClient, admin_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    old, new = uploaded(storage), uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=old)).json()

    async def broken_delete(key: str) -> None:
        raise ConnectionError("storage is down")

    storage.delete = broken_delete  # type: ignore[method-assign]
    response = await api_client.patch(
        f"{SERVERS}/{server['id']}", json={"image_key": new}, headers=admin_headers
    )

    assert response.status_code == 200, response.text


async def test_students_see_image_urls_that_stay_stable(
    api_client: AsyncClient,
    admin_headers: dict[str, str],
    student_headers: dict[str, str],
    lab_id: str,
    storage: FakeStorage,
) -> None:
    key = uploaded(storage)
    server = (await create(api_client, admin_headers, lab_id, image_key=key)).json()

    detail = await api_client.get(f"{SERVERS}/{server['id']}", headers=student_headers)
    listing = await api_client.get(SERVERS, headers=student_headers)

    assert detail.json()["image_url"] == listing.json()["items"][0]["image_url"]


async def test_students_cannot_set_images(
    api_client: AsyncClient, student_headers: dict[str, str], lab_id: str, storage: FakeStorage
) -> None:
    response = await create(api_client, student_headers, lab_id, image_key=uploaded(storage))

    assert_problem(response, 403, "forbidden")
