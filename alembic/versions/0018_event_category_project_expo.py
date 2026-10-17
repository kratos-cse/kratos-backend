"""Add PROJECT_EXPO to event_category enum."""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_category ADD VALUE IF NOT EXISTS 'PROJECT_EXPO'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values safely; leave PROJECT_EXPO in place.
    pass
