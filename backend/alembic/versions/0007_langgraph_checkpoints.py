"""langgraph checkpoints: the agent checkpointer's tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-09 12:30:00.000000

The tables belong to langgraph-checkpoint-postgres, which versions its own schema in
`checkpoint_migrations`. This migration runs the library's setup() so that every schema
change still goes through Alembic. setup() uses CREATE INDEX CONCURRENTLY, which cannot
run inside a transaction, so it runs on its own autocommit psycopg connection.

The library is pinned in pyproject.toml. An upgrade that changes its schema needs a new
migration that calls setup() again.
"""
from typing import Sequence, Union

from alembic import op
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations")


def _psycopg_url() -> str:
    url = op.get_bind().engine.url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def upgrade() -> None:
    """Upgrade schema."""
    with Connection.connect(
        _psycopg_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        PostgresSaver(conn).setup()


def downgrade() -> None:
    """Downgrade schema."""
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
