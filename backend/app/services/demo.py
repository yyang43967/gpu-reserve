"""The shared public demo account.

Every visitor signs in to the same account, so it shares one weekly quota, one set of
rate limits, and one conversation list. A daily job returns it to its seeded state:

- Bookings visitors made are deleted (their GPU allocations and notifications go with
  them). The bookings it was seeded with stay. Visitors cannot cancel those in the
  first place (`ensure_may_cancel`), so there is nothing to restore.
- Its assistant conversations are deleted, with their checkpoints, so one visitor's
  chat is not shown to the next. A conversation with a run in progress is left for the
  next reset.
- Servers whose bookings changed have their cached availability invalidated.

The caller clears the account's rate-limit counters after the commit.

Seeded and visitor bookings are told apart by time: the seed creates the account and
its bookings in one transaction, where now() does not change, so seeded bookings have
exactly the account's created_at and everything a visitor makes is later.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import column, delete, or_, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DemoAccountReadOnly
from app.models import ChatThread, Reservation, User
from app.services import audit
from app.services.availability_cache import mark_changed

# The LangGraph checkpointer's tables (not models; the library owns them), keyed by the
# thread id as text.
_CHECKPOINT_TABLES = tuple(
    table(name, column("thread_id"))
    for name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
)


@dataclass(frozen=True)
class DemoReset:
    accounts: int = 0
    reservations_deleted: int = 0
    threads_deleted: int = 0


def is_seeded(owner: User, reservation: Reservation) -> bool:
    return reservation.created_at <= owner.created_at


def ensure_may_cancel(actor: User, reservation: Reservation) -> None:
    """The demo account may cancel the bookings visitors made, not its seeded ones,
    which every later visitor should still see."""
    if actor.is_demo and reservation.user_id == actor.id and is_seeded(actor, reservation):
        raise DemoAccountReadOnly(
            "This booking is part of the demo account's history and cannot be cancelled. "
            "Bookings you make can be."
        )


async def reset_demo_accounts(
    session: AsyncSession, *, now: datetime
) -> tuple[list[uuid.UUID], DemoReset]:
    """Reset every demo account in the session's transaction. Returns their ids (for
    clearing rate limits after the commit) and what was removed. The caller commits."""
    demos = list(await session.scalars(select(User).where(User.is_demo).with_for_update()))
    reservations_deleted = threads_deleted = 0
    for user in demos:
        rows = (
            await session.execute(
                select(Reservation.id, Reservation.server_id)
                .where(Reservation.user_id == user.id, Reservation.created_at > user.created_at)
                .with_for_update()
            )
        ).all()
        if rows:
            for _, server_id in rows:
                mark_changed(session, server_id)
            await session.execute(
                delete(Reservation).where(Reservation.id.in_([row.id for row in rows]))
            )

        thread_ids = list(
            await session.scalars(
                select(ChatThread.id).where(
                    ChatThread.user_id == user.id,
                    or_(ChatThread.run_lease_until.is_(None), ChatThread.run_lease_until <= now),
                )
            )
        )
        if thread_ids:
            keys = [str(thread_id) for thread_id in thread_ids]
            for checkpoint_table in _CHECKPOINT_TABLES:
                await session.execute(
                    delete(checkpoint_table).where(checkpoint_table.c.thread_id.in_(keys))
                )
            await session.execute(delete(ChatThread).where(ChatThread.id.in_(thread_ids)))

        audit.record(
            session,
            actor_id=None,
            action="demo.reset",
            entity_type="user",
            entity_id=user.id,
            payload={"reservations_deleted": len(rows), "threads_deleted": len(thread_ids)},
        )
        reservations_deleted += len(rows)
        threads_deleted += len(thread_ids)
    await session.flush()
    return [user.id for user in demos], DemoReset(
        accounts=len(demos),
        reservations_deleted=reservations_deleted,
        threads_deleted=threads_deleted,
    )
