"""The daily reset of the shared demo account, against a seeded database: what visitors
added goes, the seeded history stays, and the account's counters start over."""

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import pytest
from redis.asyncio import Redis
from sqlalchemy import column, func, select, table, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.exceptions import DemoAccountReadOnly
from app.db.session import create_session_factory
from app.models import AuditLog, ChatMessage, ChatThread, Reservation, Server, User
from app.models.chat import ChatRole
from app.seed import run
from app.services import passwords, reservations
from app.services.availability_cache import build_availability_cache
from app.services.reservations import NewReservation
from app.workers import tasks
from tests.integration.reservation_helpers import QUOTA_ZONE, add_lab, add_server, next_saturday

pytestmark = pytest.mark.integration

DEMO_EMAIL: Final = "ada.lovelace@example.edu"
OTHER_EMAIL: Final = "alan.turing@example.edu"
PASSWORD: Final = "seed-password-for-tests"
# The checkpointer's own table; a row stands in for a conversation's saved graph state.
CHECKPOINTS = table(
    "checkpoints",
    column("thread_id"),
    column("checkpoint_ns"),
    column("checkpoint_id"),
    column("checkpoint", JSONB),
)


async def _fast_hash(password: str) -> str:
    return f"plain:{password}"


@pytest.fixture
async def seeded(
    make_settings: Callable[..., Settings],
    job_engine: AsyncEngine,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[dict[str, Any]]:
    """A seeded database with Ada as the demo account, and a worker context."""
    monkeypatch.setattr(passwords, "hash_password", _fast_hash)
    settings: Settings = make_settings(
        demo_login_email=DEMO_EMAIL, demo_login_password="demo-password-shown-on-the-page"
    )
    await run(
        settings,
        reset_first=True,
        admin_email="admin@example.edu",
        admin_password=PASSWORD,
        student_password=None,
        now=datetime.now(UTC),
    )
    redis = Redis.from_url(redis_url)
    factory = create_session_factory(
        job_engine, availability_cache=build_availability_cache(settings, redis)
    )
    yield {"settings": settings, "session_factory": factory, "cache_redis": redis}
    await redis.flushdb()
    await redis.aclose()


async def _user(db: AsyncSession, email: str) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


async def _count_reservations(factory: async_sessionmaker[AsyncSession], email: str) -> int:
    async with factory() as db:
        user = await _user(db, email)
        return int(
            await db.scalar(
                select(func.count()).select_from(Reservation).where(Reservation.user_id == user.id)
            )
            or 0
        )


async def _visitor_booking(factory: async_sessionmaker[AsyncSession], email: str) -> Server:
    """A booking made after seeding, on a server of its own so it cannot clash."""
    async with factory() as db:
        # The helpers commit as they go.
        suffix = uuid.uuid4().hex[:8]
        server = await add_server(
            db, await add_lab(db, name=f"Visitor Lab {suffix}"), f"visitor-{suffix}"
        )
        user = await _user(db, email)
        start = next_saturday(datetime.now(UTC))
        await reservations.create_reservation(
            db,
            user,
            NewReservation(
                server_id=server.id,
                gpu_count=1,
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                purpose="Trying the demo",
            ),
            now=datetime.now(UTC),
            quota_zone=QUOTA_ZONE,
        )
        await db.commit()
    return server


async def _thread(
    factory: async_sessionmaker[AsyncSession], email: str, *, running: bool = False
) -> uuid.UUID:
    async with factory() as db, db.begin():
        user = await _user(db, email)
        thread = ChatThread(
            user_id=user.id,
            title="A visitor's question",
            run_lease_until=datetime.now(UTC) + timedelta(minutes=5) if running else None,
        )
        db.add(thread)
        await db.flush()
        db.add(ChatMessage(thread_id=thread.id, role=ChatRole.USER, content="private text"))
        await db.execute(
            CHECKPOINTS.insert().values(
                thread_id=str(thread.id), checkpoint_ns="", checkpoint_id="1", checkpoint={}
            )
        )
        return thread.id


async def test_reset_removes_what_visitors_added_and_keeps_the_seeded_history(
    seeded: dict[str, Any],
) -> None:
    factory: async_sessionmaker[AsyncSession] = seeded["session_factory"]
    redis: Redis = seeded["cache_redis"]
    seeded_demo = await _count_reservations(factory, DEMO_EMAIL)
    seeded_other = await _count_reservations(factory, OTHER_EMAIL)
    server = await _visitor_booking(factory, DEMO_EMAIL)
    await _visitor_booking(factory, OTHER_EMAIL)
    visitor_thread = await _thread(factory, DEMO_EMAIL)
    running_thread = await _thread(factory, DEMO_EMAIL, running=True)
    other_thread = await _thread(factory, OTHER_EMAIL)
    async with factory() as db:
        demo_id = (await _user(db, DEMO_EMAIL)).id
        other_id = (await _user(db, OTHER_EMAIL)).id
    for user_id in (demo_id, other_id):
        await redis.set(f"assistant:86400:user:{user_id}", 200)
        await redis.set(f"assistant:60:user:{user_id}", 20)
        await redis.set(f"presign:user:{user_id}", 30)
    generation_key = f"availability:v1:{server.id}:generation"
    generation_before = int(await redis.get(generation_key) or 0)

    report = await tasks.reset_demo(seeded)

    assert report.accounts == 1
    assert report.reservations_deleted == 1
    assert report.threads_deleted == 1
    assert await _count_reservations(factory, DEMO_EMAIL) == seeded_demo
    assert await _count_reservations(factory, OTHER_EMAIL) == seeded_other + 1
    async with factory() as db:
        threads = set(await db.scalars(select(ChatThread.id)))
        assert threads == {running_thread, other_thread}
        checkpoints = set(await db.scalars(select(CHECKPOINTS.c.thread_id)))
        assert str(visitor_thread) not in checkpoints
        assert {str(running_thread), str(other_thread)} <= checkpoints
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "demo.reset"))
        assert audit is not None and audit.entity_id == demo_id
        assert audit.payload == {"reservations_deleted": 1, "threads_deleted": 1}
    # The freed GPUs show as free: the server's cached availability was invalidated.
    assert int(await redis.get(generation_key) or 0) == generation_before + 1
    # The demo account's counters start over; other accounts keep theirs.
    for prefix in ("assistant:86400", "assistant:60", "presign"):
        assert await redis.get(f"{prefix}:user:{demo_id}") is None
        assert await redis.get(f"{prefix}:user:{other_id}") is not None

    # Nothing left to do on a second run.
    again = await tasks.reset_demo(seeded)
    assert again.reservations_deleted == 0
    assert again.threads_deleted == 0


async def test_the_demo_account_cannot_cancel_its_seeded_bookings(
    seeded: dict[str, Any],
) -> None:
    factory: async_sessionmaker[AsyncSession] = seeded["session_factory"]
    await _visitor_booking(factory, DEMO_EMAIL)
    async with factory() as db, db.begin():
        demo = await _user(db, DEMO_EMAIL)
        rows = (
            await db.execute(
                select(Reservation.id, Reservation.seeded)
                .where(Reservation.user_id == demo.id)
                .order_by(Reservation.created_at)
            )
        ).all()
        seeded_id, visitor_id = rows[0].id, rows[-1].id
        assert rows[0].seeded and not rows[-1].seeded

        with pytest.raises(DemoAccountReadOnly):
            await reservations.cancel_reservation(db, demo, seeded_id, now=datetime.now(UTC))
        cancelled = await reservations.cancel_reservation(
            db, demo, visitor_id, now=datetime.now(UTC)
        )
        assert cancelled.status.value == "cancelled"


async def test_reset_does_nothing_without_a_demo_account(seeded: dict[str, Any]) -> None:
    factory: async_sessionmaker[AsyncSession] = seeded["session_factory"]
    async with factory() as db, db.begin():
        await db.execute(update(User).values(is_demo=False))
    await _visitor_booking(factory, DEMO_EMAIL)

    report = await tasks.reset_demo(seeded)

    assert report.accounts == 0
    assert report.reservations_deleted == 0
