"""GET /reservations/{id}.ics."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UserRole
from tests.integration.reservation_helpers import add_lab, add_server, booking_body, next_saturday
from tests.integration.test_admin_users import bearer, create_user, login

pytestmark = pytest.mark.integration


async def _booked(api_client: AsyncClient, db_session: AsyncSession) -> tuple[str, dict[str, str]]:
    await create_user(db_session, "student@example.edu")
    server = await add_server(db_session, await add_lab(db_session), "a100-node-01")
    headers = bearer(await login(api_client, "student@example.edu"))
    response = await api_client.post(
        "/api/v1/reservations",
        json=booking_body(server.id, next_saturday(datetime.now(UTC))),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], headers


def _lines(body: str) -> list[str]:
    return body.replace("\r\n ", "").split("\r\n")


async def test_owner_downloads_the_calendar_file(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    reservation_id, headers = await _booked(api_client, db_session)

    response = await api_client.get(f"/api/v1/reservations/{reservation_id}.ics", headers=headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="reservation-{reservation_id}.ics"'
    )
    lines = _lines(response.text)
    assert f"UID:{reservation_id}@gpu-reserve" in lines
    assert "SEQUENCE:0" in lines
    assert "STATUS:CONFIRMED" in lines
    assert "SUMMARY:1 GPU on a100-node-01" in lines


async def test_sequence_increases_when_the_reservation_changes(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    reservation_id, headers = await _booked(api_client, db_session)
    cancel = await api_client.post(f"/api/v1/reservations/{reservation_id}/cancel", headers=headers)
    assert cancel.status_code == 200, cancel.text

    response = await api_client.get(f"/api/v1/reservations/{reservation_id}.ics", headers=headers)

    lines = _lines(response.text)
    assert "SEQUENCE:1" in lines
    assert "STATUS:CANCELLED" in lines


async def test_admins_cannot_download_other_peoples_calendar_files(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    reservation_id, _ = await _booked(api_client, db_session)
    await create_user(db_session, "admin@example.edu", UserRole.ADMIN)
    admin_headers = bearer(await login(api_client, "admin@example.edu"))

    response = await api_client.get(
        f"/api/v1/reservations/{reservation_id}.ics", headers=admin_headers
    )

    assert response.status_code == 404
    # The JSON view still works for admins.
    plain = await api_client.get(f"/api/v1/reservations/{reservation_id}", headers=admin_headers)
    assert plain.status_code == 200


async def test_a_malformed_id_is_a_validation_error(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    _, headers = await _booked(api_client, db_session)

    response = await api_client.get("/api/v1/reservations/not-a-uuid.ics", headers=headers)

    assert response.status_code == 422
