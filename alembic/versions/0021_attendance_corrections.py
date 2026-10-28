"""Append-only attendance corrections for reversible check-in."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attendance_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("checkpoint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attendance_checkpoints.id"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=True),
        sa.Column("team_member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "profile_id IS NOT NULL OR team_member_id IS NOT NULL",
            name="ck_attendance_corrections_participant_identity",
        ),
    )
    op.create_index("ix_attendance_corrections_checkpoint_id", "attendance_corrections", ["checkpoint_id"])
    op.create_index("ix_attendance_corrections_profile_id", "attendance_corrections", ["profile_id"])
    op.create_index("ix_attendance_corrections_team_member_id", "attendance_corrections", ["team_member_id"])


def downgrade() -> None:
    op.drop_index("ix_attendance_corrections_team_member_id", table_name="attendance_corrections")
    op.drop_index("ix_attendance_corrections_profile_id", table_name="attendance_corrections")
    op.drop_index("ix_attendance_corrections_checkpoint_id", table_name="attendance_corrections")
    op.drop_table("attendance_corrections")
