"""Database helpers shared by integration fixtures and tests."""

from pathlib import Path

from alembic.config import Config
from sqlalchemy import NullPool, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.base import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # ConfigParser treats % as interpolation syntax.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    # pytest owns logging; don't let alembic.ini reconfigure it.
    config.attributes["configure_logging"] = False
    return config


def with_database(database_url: str, name: str) -> str:
    return make_url(database_url).set(database=name).render_as_string(hide_password=False)


async def create_database(admin_url: str, name: str) -> str:
    """Create an empty database next to `admin_url` and return its URL."""
    engine = create_async_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()
    return with_database(admin_url, name)


async def truncate_all(engine: AsyncEngine) -> None:
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        # Fail fast instead of hanging if a test left a transaction open.
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
