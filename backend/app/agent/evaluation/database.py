"""A fresh database for every case, copied from a template built once per run.

The template is migrated, seeded at EVAL_NOW, and holds the knowledge base. Copying it
with `CREATE DATABASE ... TEMPLATE` takes a fraction of a second, gives each case its
own checkpoint tables too, and needs no cleanup between cases beyond a DROP.

Postgres refuses to copy a database that has open connections, so the template's
engine is disposed after the build and the template is closed to new connections.
"""

import asyncio
import contextlib
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import NullPool, make_url, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.evaluation.cases import EVAL_NOW, Reserve, SetServerNotes, Setup
from app.db.session import create_session_factory
from app.models import AssistantAction, Reservation, Server, User, UserRole
from app.rag.embeddings import Embedder
from app.rag.ingest import ingest
from app.seed import DEFAULT_ADMIN_EMAIL, seed
from app.services import jobs, reservations
from app.services.reservations import NewReservation

BACKEND_DIR: Final = Path(__file__).resolve().parents[3]
# Overlay reservations are booked this long before they start, and approved an hour
# after that.
BOOKED_AHEAD: Final = timedelta(days=1)
DECISION_DELAY: Final = timedelta(hours=1)
CASE_POOL_SIZE: Final = 5


class EvalDatabaseError(RuntimeError):
    pass


def with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _admin_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT")


async def check_can_create_databases(admin_url: str) -> None:
    engine = _admin_engine(admin_url)
    try:
        async with engine.connect() as conn:
            allowed = await conn.scalar(
                text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
    finally:
        await engine.dispose()
    if not allowed:
        raise EvalDatabaseError(
            "The database role cannot create databases. The evaluation copies a template "
            "database for every case; grant CREATEDB or point DATABASE_URL at a role "
            "that has it."
        )


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    # ConfigParser treats % as interpolation syntax.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    return config


@dataclass
class Databases:
    """The template and the case copies of one run, all next to `admin_url`."""

    admin_url: str
    prefix: str

    def __post_init__(self) -> None:
        self._create_lock = asyncio.Lock()

    @property
    def template(self) -> str:
        return f"{self.prefix}_template"

    def url(self, name: str) -> str:
        return with_database(self.admin_url, name)

    async def _execute(self, *statements: str) -> None:
        engine = _admin_engine(self.admin_url)
        try:
            async with engine.connect() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
        finally:
            await engine.dispose()

    async def build_template(
        self, embedder: Embedder, kb_dir: Path, *, quota_zone: ZoneInfo
    ) -> None:
        name = self.template
        await self._execute(
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}"'
        )
        url = self.url(name)
        # Alembic's env runs its own event loop, so it gets a thread without one.
        await asyncio.to_thread(command.upgrade, _alembic_config(url), "head")
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            factory = create_session_factory(engine)
            async with factory() as session, session.begin():
                await seed(
                    session,
                    now=EVAL_NOW,
                    admin_email=DEFAULT_ADMIN_EMAIL,
                    admin_password=secrets.token_urlsafe(24),
                    student_password=None,
                    quota_zone=quota_zone,
                )
            await ingest(factory, embedder, kb_dir)
        finally:
            await engine.dispose()
        await self._execute(f'ALTER DATABASE "{name}" WITH ALLOW_CONNECTIONS false')
        engine = _admin_engine(self.admin_url)
        try:
            async with engine.connect() as conn:
                open_connections = await conn.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": name},
                )
        finally:
            await engine.dispose()
        if open_connections:
            raise EvalDatabaseError(f"{open_connections} connections to {name} are still open.")

    @contextlib.asynccontextmanager
    async def case_database(self, index: int) -> AsyncIterator[str]:
        name = f"{self.prefix}_case_{index}"
        async with self._create_lock:
            # Copies from one template are made one at a time.
            await self._execute(
                f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)',
                f'CREATE DATABASE "{name}" TEMPLATE "{self.template}"',
            )
        try:
            yield self.url(name)
        finally:
            await self._execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    async def drop_template(self) -> None:
        await self._execute(f'DROP DATABASE IF EXISTS "{self.template}" WITH (FORCE)')


def case_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_size=CASE_POOL_SIZE, max_overflow=CASE_POOL_SIZE)


# --- names to rows ---


async def user_by_email(session: AsyncSession, email: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise EvalDatabaseError(f"No user {email} in the seed data.")
    return user


async def server_by_hostname(session: AsyncSession, hostname: str) -> Server:
    server = await session.scalar(select(Server).where(Server.hostname == hostname))
    if server is None:
        raise EvalDatabaseError(f"No server {hostname} in the seed data.")
    return server


async def reservation_by_purpose(session: AsyncSession, purpose: str) -> Reservation:
    rows = (await session.scalars(select(Reservation).where(Reservation.purpose == purpose))).all()
    if len(rows) != 1:
        raise EvalDatabaseError(f"{len(rows)} reservations have the purpose {purpose!r}.")
    return rows[0]


async def server_ids(session: AsyncSession) -> dict[str, uuid.UUID]:
    rows = await session.execute(select(Server.hostname, Server.id))
    return {hostname: server_id for hostname, server_id in rows}


async def reservation_ids(session: AsyncSession) -> dict[uuid.UUID, str]:
    rows = await session.execute(select(Reservation.id, Reservation.purpose))
    return {reservation_id: purpose for reservation_id, purpose in rows}


# --- overlays ---


async def _reserve(session: AsyncSession, op: Reserve, *, quota_zone: ZoneInfo) -> None:
    user = await user_by_email(session, op.user)
    server = await server_by_hostname(session, op.server)
    booked_at = op.start - BOOKED_AHEAD
    reservation = await reservations.create_reservation(
        session,
        user,
        NewReservation(
            server_id=server.id,
            gpu_count=op.gpu_count,
            starts_at=op.start,
            ends_at=op.end,
            purpose=op.purpose,
        ),
        now=booked_at,
        quota_zone=quota_zone,
    )
    if op.approve:
        admin = await session.scalar(select(User).where(User.role == UserRole.ADMIN))
        if admin is None:
            raise EvalDatabaseError("The seed data has no admin.")
        await reservations.approve_reservation(
            session, admin, reservation.id, now=booked_at + DECISION_DELAY
        )


async def apply_setup(
    factory: async_sessionmaker[AsyncSession],
    ops: list[Setup],
    *,
    now: datetime,
    quota_zone: ZoneInfo,
) -> None:
    """Apply overlay operations through the services, then let the jobs catch up to
    `now` as the seed does, so a booking that has already ended is completed."""
    if not ops:
        return
    async with factory() as session, session.begin():
        for op in ops:
            if isinstance(op, Reserve):
                await _reserve(session, op, quota_zone=quota_zone)
            elif isinstance(op, SetServerNotes):
                server = await server_by_hostname(session, op.server)
                server.notes = op.notes
        await jobs.expire_pending(session, now=now)
        await jobs.complete_finished(session, now=now)


# --- what changed ---


async def reservation_snapshot(
    factory: async_sessionmaker[AsyncSession],
) -> dict[str, dict[str, Any]]:
    async with factory() as session:
        rows = await session.execute(
            select(Reservation, User.email, Server.hostname)
            .join(User, Reservation.user_id == User.id)
            .join(Server, Reservation.server_id == Server.id)
        )
        return {
            str(reservation.id): {
                "user": email,
                "server": hostname,
                "gpu_count": reservation.gpu_count,
                "start": reservation.starts_at,
                "end": reservation.ends_at,
                "status": reservation.status.value,
                "purpose": reservation.purpose,
            }
            for reservation, email, hostname in rows
        }


async def action_outcomes(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(
            select(AssistantAction.outcome).order_by(AssistantAction.created_at, AssistantAction.id)
        )
        return [outcome.value for outcome in rows]
