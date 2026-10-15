"""Persist stable Google Sheets worksheet tab title per event."""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("google_sheets_worksheet_title", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("events", "google_sheets_worksheet_title")
