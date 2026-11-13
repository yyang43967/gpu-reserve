"""users.seeded and reservations.seeded: demo data the nightly replay may replace

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13 22:00:00.000000

Additive only, with server defaults, so the previous release keeps working against
this schema (it neither reads nor writes the columns).

Backfill: the seed only runs on an empty database and creates every user and booking
in one transaction, where now() does not change. So the seeded students share the
created_at of the first user, and their seeded bookings share it too. Admins are never
marked, and neither are their bookings: the admin account belongs to a real person.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('seeded', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        'reservations',
        sa.Column('seeded', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        """
        UPDATE users SET seeded = true
        WHERE role = 'student'
          AND created_at = (SELECT min(created_at) FROM users)
        """
    )
    op.execute(
        """
        UPDATE reservations AS r SET seeded = true
        FROM users AS u
        WHERE r.user_id = u.id AND u.seeded AND r.created_at = u.created_at
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reservations', 'seeded')
    op.drop_column('users', 'seeded')
