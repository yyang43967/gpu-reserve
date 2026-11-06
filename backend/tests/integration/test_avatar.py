import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from tests.fakes import FakeStorage
from tests.integration.test_admin_users import assert_problem, bearer, create_user, login

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
USERS = "/api/v1/users"
AUTH = "/api/v1/auth"
PASSWORD = "correct-horse-battery"
MIB = 1024 * 1024


@pytest.fixture
def storage(running_app: FastAPI) -> FakeStorage:
    fake = FakeStorage()
    running_app.state.storage = fake
    return fake


@pytest.fixture
async def student(db_session: AsyncSession) -> User:
    return await create_user(db_session, "student@example.edu")


@pytest.fixture
async def headers(api_client: AsyncClient, student: User) -> dict[str, str]:
    return bearer(await login(api_client, "student@example.edu"))


def uploaded(
    storage: FakeStorage, owner: User, size: int = 1000, content_type: str = "image/png"
) -> str:
    key = f"avatars/{owner.id}/{uuid.uuid4().hex}.png"
    storage.put(key, size, content_type)
    return key


async def set_avatar(
    client: AsyncClient, headers: dict[str, str], key: str | None, **fields: object
) -> dict[str, object]:
    response = await client.patch(ME, json={"avatar_key": key, **fields}, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def test_set_avatar(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    key = uploaded(storage, student)

    body = await set_avatar(api_client, headers, key)

    assert body["avatar_key"] == key
    assert str(body["avatar_url"]).startswith(f"http://storage.test/uploads/{key}?")
    me = (await api_client.get(ME, headers=headers)).json()
    assert me["avatar_url"] == body["avatar_url"]


async def test_avatar_url_appears_wherever_the_user_is_returned(
    api_client: AsyncClient,
    headers: dict[str, str],
    student: User,
    storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    key = uploaded(storage, student)
    await set_avatar(api_client, headers, key)
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    admin_headers = bearer(await login(api_client, "admin@example.edu"))

    login_body = (await login(api_client, "student@example.edu")).json()
    listed = (await api_client.get(USERS, params={"q": "student"}, headers=admin_headers)).json()
    single = (await api_client.get(f"{USERS}/{student.id}", headers=admin_headers)).json()

    assert key in login_body["user"]["avatar_url"]
    assert key in listed["items"][0]["avatar_url"]
    assert key in single["avatar_url"]


async def test_user_without_avatar(
    api_client: AsyncClient, headers: dict[str, str], storage: FakeStorage
) -> None:
    me = (await api_client.get(ME, headers=headers)).json()

    assert me["avatar_key"] is None
    assert me["avatar_url"] is None


async def test_name_and_avatar_together(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    key = uploaded(storage, student)

    body = await set_avatar(api_client, headers, key, full_name="Ada L.")

    assert body["full_name"] == "Ada L."
    assert body["avatar_key"] == key


async def test_rename_keeps_the_avatar(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    key = uploaded(storage, student)
    await set_avatar(api_client, headers, key)

    response = await api_client.patch(ME, json={"full_name": "Ada L."}, headers=headers)

    assert response.json()["avatar_key"] == key
    assert storage.deleted == []


async def test_another_users_avatar_is_rejected(
    api_client: AsyncClient,
    headers: dict[str, str],
    storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    other = await create_user(db_session, "other@example.edu")
    key = uploaded(storage, other)

    response = await api_client.patch(ME, json={"avatar_key": key}, headers=headers)

    assert_problem(response, 422, "invalid-upload")
    assert storage.deleted == []
    assert key in storage.objects


@pytest.mark.parametrize(
    "key",
    ["servers/abc.png", "avatars/abc.png", "abc.png", "avatars/{me}/nested/abc.png"],
)
async def test_keys_outside_the_callers_prefix_are_rejected(
    api_client: AsyncClient,
    headers: dict[str, str],
    student: User,
    storage: FakeStorage,
    key: str,
) -> None:
    key = key.format(me=student.id)
    storage.put(key, 1000, "image/png")

    response = await api_client.patch(ME, json={"avatar_key": key}, headers=headers)

    assert_problem(response, 422, "invalid-upload")


async def test_missing_upload(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    response = await api_client.patch(
        ME, json={"avatar_key": f"avatars/{student.id}/never.png"}, headers=headers
    )

    assert_problem(response, 422, "upload-not-found")


@pytest.mark.parametrize(
    ("size", "content_type"), [(2 * MIB + 1, "image/png"), (1000, "image/svg+xml")]
)
async def test_bad_upload_is_rejected_and_deleted(
    api_client: AsyncClient,
    headers: dict[str, str],
    student: User,
    storage: FakeStorage,
    size: int,
    content_type: str,
) -> None:
    key = uploaded(storage, student, size=size, content_type=content_type)

    response = await api_client.patch(
        ME, json={"avatar_key": key, "full_name": "Not Saved"}, headers=headers
    )

    assert_problem(response, 422, "invalid-upload")
    assert storage.deleted == [key]
    me = (await api_client.get(ME, headers=headers)).json()
    assert me["avatar_key"] is None
    assert me["full_name"] != "Not Saved"


async def test_replacing_deletes_the_old_avatar(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    old, new = uploaded(storage, student), uploaded(storage, student)
    await set_avatar(api_client, headers, old)

    await set_avatar(api_client, headers, new)

    assert storage.deleted == [old]
    assert new in storage.objects


async def test_same_key_deletes_nothing(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    key = uploaded(storage, student)
    await set_avatar(api_client, headers, key)

    await set_avatar(api_client, headers, key)

    assert storage.deleted == []
    assert key in storage.objects


async def test_null_removes_the_avatar(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    key = uploaded(storage, student)
    await set_avatar(api_client, headers, key)

    body = await set_avatar(api_client, headers, None)

    assert body["avatar_key"] is None
    assert body["avatar_url"] is None
    assert storage.deleted == [key]


async def test_failed_update_keeps_the_old_avatar(
    api_client: AsyncClient, headers: dict[str, str], student: User, storage: FakeStorage
) -> None:
    old = uploaded(storage, student)
    await set_avatar(api_client, headers, old)

    response = await api_client.patch(
        ME, json={"avatar_key": f"avatars/{student.id}/never.png"}, headers=headers
    )

    assert_problem(response, 422, "upload-not-found")
    assert storage.deleted == []
    assert (await api_client.get(ME, headers=headers)).json()["avatar_key"] == old


async def test_deleting_a_user_deletes_their_avatar(
    api_client: AsyncClient,
    headers: dict[str, str],
    student: User,
    storage: FakeStorage,
    db_session: AsyncSession,
) -> None:
    key = uploaded(storage, student)
    await set_avatar(api_client, headers, key)
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    admin_headers = bearer(await login(api_client, "admin@example.edu"))

    response = await api_client.delete(f"{USERS}/{student.id}", headers=admin_headers)

    assert response.status_code == 204
    assert storage.deleted == [key]


async def test_profile_still_rejects_unknown_fields(
    api_client: AsyncClient, headers: dict[str, str], storage: FakeStorage
) -> None:
    response = await api_client.patch(ME, json={"avatar_url": "http://x/a.png"}, headers=headers)

    assert response.status_code == 422
