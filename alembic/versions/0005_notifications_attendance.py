"""Notifications + attendance checkpoints and immutable scan log.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_NOTIFICATION_KIND = [
    "PAYMENT_CONFIRMATION",
    "REGISTRATION_CONFIRMATION",
    "MEMBER_JOINED",
    "MEMBER_CONFIRMATION",
    "TEAM_COMPLETED",
    "REFUND",
    "ANNOUNCEMENT",
    "REMINDER",
]
_NOTIFICATION_STATUS = ["PENDING", "SENT", "FAILED"]
_ATTENDANCE_SCAN_RESULT = ["SUCCESS", "DUPLICATE", "INVALID", "NOT_PAID"]
_DUPLICATE_SCAN_BEHAVIOR = ["REJECT", "ACCEPT", "WARN"]


def upgrade() -> None:
    bind = op.get_bind()

    notification_kind = postgresql.ENUM(*_NOTIFICATION_KIND, name="notification_kind", create_type=False)
    notification_status = postgresql.ENUM(*_NOTIFICATION_STATUS, name="notification_status", create_type=False)
    attendance_scan_result = postgresql.ENUM(*_ATTENDANCE_SCAN_RESULT, name="attendance_scan_result", create_type=False)
    duplicate_scan_behavior = postgresql.ENUM(*_DUPLICATE_SCAN_BEHAVIOR, name="duplicate_scan_behavior", create_type=False)

    for enum_type in (
        notification_kind,
        notification_status,
        attendance_scan_result,
        duplicate_scan_behavior,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("kind", notification_kind, nullable=False),
        sa.Column("channel", sa.String(), nullable=False, server_default="EMAIL"),
        sa.Column("status", notification_status, nullable=False, server_default="PENDING"),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_profile_id", "notifications", ["profile_id"])
    op.create_index("ix_notifications_payment_id", "notifications", ["payment_id"])
    op.create_index("ix_notifications_registration_id", "notifications", ["registration_id"])
    op.create_index("ix_notifications_team_id", "notifications", ["team_id"])

    op.create_table(
        "attendance_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("allow_repeat_scan", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "duplicate_scan_behavior",
            duplicate_scan_behavior,
            nullable=False,
            server_default="REJECT",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_attendance_checkpoints_event_id", "attendance_checkpoints", ["event_id"])

    op.create_table(
        "attendance_scans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "checkpoint_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attendance_checkpoints.id"),
            nullable=False,
        ),
        sa.Column("qr_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("qr_codes.id"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("result", attendance_scan_result, nullable=False),
        sa.Column(
            "scanned_by_admin_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_attendance_scans_checkpoint_id", "attendance_scans", ["checkpoint_id"])
    op.create_index("ix_attendance_scans_qr_id", "attendance_scans", ["qr_id"])
    op.create_index("ix_attendance_scans_profile_id", "attendance_scans", ["profile_id"])
    op.create_index(
        "ix_attendance_scans_checkpoint_profile",
        "attendance_scans",
        ["checkpoint_id", "profile_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_attendance_scans_checkpoint_profile", table_name="attendance_scans")
    op.drop_index("ix_attendance_scans_profile_id", table_name="attendance_scans")
    op.drop_index("ix_attendance_scans_qr_id", table_name="attendance_scans")
    op.drop_index("ix_attendance_scans_checkpoint_id", table_name="attendance_scans")
    op.drop_table("attendance_scans")

    op.drop_index("ix_attendance_checkpoints_event_id", table_name="attendance_checkpoints")
    op.drop_table("attendance_checkpoints")

    op.drop_index("ix_notifications_team_id", table_name="notifications")
    op.drop_index("ix_notifications_registration_id", table_name="notifications")
    op.drop_index("ix_notifications_payment_id", table_name="notifications")
    op.drop_index("ix_notifications_profile_id", table_name="notifications")
    op.drop_table("notifications")

    bind = op.get_bind()
    postgresql.ENUM(name="duplicate_scan_behavior").drop(bind, checkfirst=True)
    postgresql.ENUM(name="attendance_scan_result").drop(bind, checkfirst=True)
    postgresql.ENUM(name="notification_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="notification_kind").drop(bind, checkfirst=True)
