import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import NullPool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import CHECKPOINT_TABLES, Base, include_name
from tests.integration.db_utils import alembic_config, create_database

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
