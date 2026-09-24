"""Attendance scans: record team_member_id for leader-entered participants.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-24
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "attendance_scans",
        sa.Column("team_member_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_attendance_scans_team_member_id",
        "attendance_scans",
        "team_members",
        ["team_member_id"],
        ["id"],
    )
    op.create_index("ix_attendance_scans_team_member_id", "attendance_scans", ["team_member_id"])
    op.create_index(
        "ix_attendance_scans_checkpoint_team_member",
        "attendance_scans",
        ["checkpoint_id", "team_member_id"],
    )
    op.alter_column("attendance_scans", "profile_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.create_check_constraint(
        "ck_attendance_scans_participant_identity",
        "attendance_scans",
        "profile_id IS NOT NULL OR team_member_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attendance_scans_participant_identity", "attendance_scans", type_="check")
    op.alter_column("attendance_scans", "profile_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_attendance_scans_checkpoint_team_member", table_name="attendance_scans")
    op.drop_index("ix_attendance_scans_team_member_id", table_name="attendance_scans")
    op.drop_constraint("fk_attendance_scans_team_member_id", "attendance_scans", type_="foreignkey")
    op.drop_column("attendance_scans", "team_member_id")
