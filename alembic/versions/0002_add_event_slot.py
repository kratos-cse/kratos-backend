"""Add slot (morning/afternoon/etc.) to events

New field on top of starts_at/ends_at, added at the user's request so an
organizer can explicitly label a session rather than only deriving it from
the raw start time. NOT part of the original finalized schema doc — flag
this to the team before merging, since `events` is a table other feature
branches also read.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_VALUES = ["MORNING", "AFTERNOON", "EVENING", "FULL_DAY", "MULTI_DAY"]


def upgrade() -> None:
    bind = op.get_bind()
    event_slot = postgresql.ENUM(*_VALUES, name="event_slot", create_type=False)
    event_slot.create(bind, checkfirst=True)

    op.add_column("events", sa.Column("slot", event_slot, nullable=True))


def downgrade() -> None:
    op.drop_column("events", "slot")

    bind = op.get_bind()
    postgresql.ENUM(name="event_slot").drop(bind, checkfirst=True)