import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import NullPool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import CHECKPOINT_TABLES, Base, include_name
from app.seed import STUDENTS, seed
from app.services import passwords
from app.services.demo_plan import DEMO_ADMIN_EMAIL
from tests.integration.db_utils import alembic_config, create_database
from tests.integration.reservation_helpers import QUOTA_ZONE

pytestmark = pytest.mark.integration


async def _schema_diff(url: str) -> list[Any]:
    def compare(conn: Connection) -> list[Any]:
        opts = {"compare_type": True, "include_name": include_name}
        context = MigrationContext.configure(conn, opts=opts)
        return list(compare_metadata(context, Base.metadata))

    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        diff = await conn.run_sync(compare)
    await engine.dispose()
    return diff


async def _leftovers(url: str) -> dict[str, list[str]]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        tables = await conn.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        )
        types = await conn.scalars(
            text(
                "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                "WHERE n.nspname = 'public' AND t.typtype = 'e' ORDER BY 1"
            )
        )
        extensions = await conn.scalars(
            text("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1")
        )
        result = {
            "tables": list(tables),
            "enums": list(types),
            "extensions": list(extensions),
        }
    await engine.dispose()
    return result


def test_migrations_match_models_and_downgrade_cleanly(postgres_url: str) -> None:
    # A separate database, so the shared session database is not touched.
    url = asyncio.run(create_database(postgres_url, "migration_check"))
    config = alembic_config(url)

    command.upgrade(config, "head")
    assert asyncio.run(_schema_diff(url)) == []
    # The checkpointer's tables come from its own setup(), run by migration 0007.
    assert set(asyncio.run(_leftovers(url))["tables"]) >= CHECKPOINT_TABLES
    assert asyncio.run(_leftovers(url))["extensions"] == ["btree_gist", "vector"]

    command.downgrade(config, "base")
    assert asyncio.run(_leftovers(url)) == {
        "tables": ["alembic_version"],
        "enums": [],
        "extensions": [],
    }

    # Upgrading again proves the downgrade left nothing behind that would collide.
    command.upgrade(config, "head")


async def _seed_at_head(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    async with async_sessionmaker(engine)() as session, session.begin():
        await seed(
            session,
            now=datetime(2030, 1, 9, 17, 40, tzinfo=UTC),
            admin_email="admin@example.edu",
            admin_password="seed-password-for-tests",
            student_password=None,
            quota_zone=QUOTA_ZONE,
        )
    await engine.dispose()


async def _add_registered_user_and_booking(url: str) -> None:
    """What happens after seeding: someone registers, a student books again."""
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO users (email, password_hash, full_name, role) "
                "SELECT 'late@example.org', password_hash, 'Late Registrant', role "
                "FROM users WHERE role = 'student' LIMIT 1"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO reservations "
                "(user_id, server_id, gpu_count, starts_at, ends_at, status, purpose) "
                "SELECT user_id, server_id, gpu_count, starts_at, ends_at, 'cancelled', "
                "'Booked after seeding' FROM reservations "
                "WHERE user_id = (SELECT id FROM users WHERE email = 'ada.lovelace@example.edu') "
                "LIMIT 1"
            )
        )
    await engine.dispose()


async def _flags(url: str) -> tuple[set[tuple[str, str, bool]], set[tuple[str, str, bool]]]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        users = {
            (row.email, row.role, row.seeded)
            for row in await conn.execute(text("SELECT email, role::text, seeded FROM users"))
        }
        bookings = {
            (row.email, row.purpose, row.seeded)
            for row in await conn.execute(
                text(
                    "SELECT u.email, r.purpose, r.seeded "
                    "FROM reservations r JOIN users u ON u.id = r.user_id"
                )
            )
        }
    await engine.dispose()
    return users, bookings


def test_backfill_marks_only_seeded_students_and_their_bookings(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fast_hash(password: str) -> str:
        return f"plain:{password}"

    monkeypatch.setattr(passwords, "hash_password", fast_hash)
    url = asyncio.run(create_database(postgres_url, "seeded_backfill"))
    config = alembic_config(url)
    command.upgrade(config, "head")
    asyncio.run(_seed_at_head(url))
    # A database seeded before the flags existed, with activity since.
    command.downgrade(config, "0008")
    asyncio.run(_add_registered_user_and_booking(url))

    command.upgrade(config, "head")

    users, bookings = asyncio.run(_flags(url))
    # The backfill never marks an admin. (Production was seeded before the demo admin
    # existed; the refresh creates it.)
    assert users == {
        ("admin@example.edu", "admin", False),
        (DEMO_ADMIN_EMAIL, "admin", False),
        ("late@example.org", "student", False),
        *((email, "student", True) for _, email in STUDENTS),
    }
    admins = {"admin@example.edu", DEMO_ADMIN_EMAIL}
    for email, purpose, seeded in bookings:
        expected = email not in admins and purpose != "Booked after seeding"
        assert seeded is expected, (email, purpose)
    assert ("ada.lovelace@example.edu", "Booked after seeding", False) in bookings
