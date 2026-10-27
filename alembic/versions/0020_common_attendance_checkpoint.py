"""Allow a checkpoint with no event so it can be used for every event."""

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "attendance_checkpoints",
        "event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM attendance_scans WHERE checkpoint_id IN (SELECT id FROM attendance_checkpoints WHERE event_id IS NULL)")
    op.execute("DELETE FROM attendance_checkpoints WHERE event_id IS NULL")
    op.alter_column(
        "attendance_checkpoints",
        "event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
