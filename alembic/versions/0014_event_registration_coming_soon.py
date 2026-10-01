"""Add COMING_SOON to event_registration_status enum.

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_registration_status ADD VALUE IF NOT EXISTS 'COMING_SOON'")


def downgrade() -> None:
    op.execute(
        """
        UPDATE events
        SET registration_status = 'CLOSED'
        WHERE registration_status = 'COMING_SOON'
        """
    )
    # PostgreSQL cannot remove enum values without recreating the type.
