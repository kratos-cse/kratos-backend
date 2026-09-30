"""Rename event_category value CULTURAL -> TITLE_EVENT.

Revision ID: 0013
Revises: 0012

Deploy the backend build containing EventCategory.TITLE_EVENT before running
this migration; the old build would fail to read rows once the label changes.
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_category RENAME VALUE 'CULTURAL' TO 'TITLE_EVENT'")


def downgrade() -> None:
    op.execute("ALTER TYPE event_category RENAME VALUE 'TITLE_EVENT' TO 'CULTURAL'")
