import asyncio
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cli
from app.models import RefreshToken, User, UserRole
from app.services import users
from tests.integration.db_utils import BACKEND_DIR

pytestmark = pytest.mark.integration

PASSWORD = "admin-bootstrap-password"
AUTH = "/api/v1/auth"


@pytest.fixture(autouse=True)
def cli_environment(monkeypatch: pytest.MonkeyPatch, migrated_database_url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("ADMIN_PASSWORD", PASSWORD)


async def run_cli(*args: str) -> int:
    # main() calls asyncio.run, so it needs a thread without a running event loop.
    return await asyncio.to_thread(cli.main, ["create-admin", *args])


async def all_users(db: AsyncSession) -> list[User]:
    rows = await db.scalars(select(User).execution_options(populate_existing=True))
    return list(rows)


async def login_status(client: AsyncClient, email: str, password: str) -> int:
    response = await client.post(f"{AUTH}/login", json={"email": email, "password": password})
    return response.status_code


async def test_module_entry_point_creates_an_admin(
    migrated_database_url: str, db_session: AsyncSession, api_client: AsyncClient
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.cli",
        "create-admin",
        "--email",
        "Root@Example.EDU",
        "--name",
        "Grace Hopper",
        cwd=BACKEND_DIR,
        env={**os.environ, "DATABASE_URL": migrated_database_url, "ADMIN_PASSWORD": PASSWORD},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    assert process.returncode == 0, stderr.decode()
    assert stdout.decode().strip() == "Created admin root@example.edu."
    assert PASSWORD not in stdout.decode() + stderr.decode()
    [user] = await all_users(db_session)
    assert (user.email, user.full_name, user.role) == (
        "root@example.edu",
        "Grace Hopper",
        UserRole.ADMIN,
    )
    assert await login_status(api_client, "root@example.edu", PASSWORD) == 200


async def test_promotes_an_existing_account(
    db_session: AsyncSession, api_client: AsyncClient
) -> None:
    student = await users.create_user(
        db_session, email="ada@example.edu", password="old-password-1", full_name="Ada"
    )
    student.is_active = False
    await db_session.commit()
    # A live session from before the reset.
    db_session.add(
        RefreshToken(
            user_id=student.id,
            token_hash="a" * 64,
            family_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
    )
    await db_session.commit()

    exit_code = await run_cli("--email", "ADA@example.edu")

    assert exit_code == cli.EXIT_OK
    [user] = await all_users(db_session)  # promoted, not duplicated
    assert (user.role, user.is_active, user.full_name) == (UserRole.ADMIN, True, "Ada")
    live = await db_session.scalar(
        select(func.count()).select_from(RefreshToken).where(RefreshToken.revoked_at.is_(None))
    )
    assert live == 0
    assert await login_status(api_client, "ada@example.edu", PASSWORD) == 200
    assert await login_status(api_client, "ada@example.edu", "old-password-1") == 401


async def test_short_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "short")

    exit_code = await run_cli("--email", "root@example.edu", "--name", "Root")

    assert exit_code == cli.EXIT_ERROR
    assert "8 to 128 characters" in capsys.readouterr().err
    assert await all_users(db_session) == []


async def test_new_account_needs_a_name(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = await run_cli("--email", "root@example.edu")

    assert exit_code == cli.EXIT_ERROR
    assert "full name is required" in capsys.readouterr().err
    assert await all_users(db_session) == []


async def test_invalid_email_is_rejected(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = await run_cli("--email", "not-an-email", "--name", "Root")

    assert exit_code == cli.EXIT_ERROR
    assert "Not a valid email address" in capsys.readouterr().err
    assert await all_users(db_session) == []


def _prompts(answers: list[str]) -> Iterator[str]:
    yield from answers


async def test_prompts_for_the_password_when_env_is_unset(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD")
    answers = _prompts([PASSWORD, PASSWORD])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert await run_cli("--email", "root@example.edu", "--name", "Root") == cli.EXIT_OK
    [user] = await all_users(db_session)
    assert user.role is UserRole.ADMIN


async def test_mismatched_prompts_are_rejected(
    monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ADMIN_PASSWORD")
    answers = _prompts([PASSWORD, "something-else"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert await run_cli("--email", "root@example.edu", "--name", "Root") == cli.EXIT_ERROR
    assert "do not match" in capsys.readouterr().err
    assert await all_users(db_session) == []


async def test_password_is_not_an_argument() -> None:
    with pytest.raises(SystemExit):
        cli.main(["create-admin", "--email", "root@example.edu", "--password", PASSWORD])
