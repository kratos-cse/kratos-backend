"""Event admin assignments + granular RBAC roles/permissions.

Revision ID: 0012
Revises: 0011
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SUPER_ADMIN_ROLE_ID = "00000000-0000-4000-8000-000000000001"
ADMIN_ROLE_ID = "00000000-0000-4000-8000-000000000002"
EVENT_COORDINATOR_ROLE_ID = "00000000-0000-4000-8000-000000000003"

NEW_SUPER_ADMIN_PERMISSIONS = [
    "event-read",
    "event-edit",
    "event-control",
    "event-assignment-management",
    "admin-management",
    "role-management",
]

ADMIN_ROLE_PERMISSIONS = [
    "dashboard",
    "event-read",
    "event-edit",
    "participant-read",
    "participant-edit",
    "participant-registration",
    "team-read",
    "team-edit",
    "leadership-transfer",
    "registration-read",
    "registration-edit",
    "payment-read",
    "attendance-read",
    "attendance-scan",
    "checkpoint-management",
    "manual-attendance",
    "announcement",
    "reminder",
    "export",
    "notification",
]

EVENT_COORDINATOR_PERMISSIONS = [
    "dashboard",
    "event-read",
    "registration-read",
    "team-read",
    "participant-read",
]


def upgrade() -> None:
    op.create_table(
        "event_admin_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignment_type", sa.String(length=64), nullable=False, server_default="EVENT_COORDINATOR"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "created_by_admin_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("event_id", "admin_user_id", name="uq_event_admin_assignments_event_admin"),
    )
    op.create_index("ix_event_admin_assignments_event_id", "event_admin_assignments", ["event_id"])
    op.create_index("ix_event_admin_assignments_admin_user_id", "event_admin_assignments", ["admin_user_id"])

    # New granular permissions for SUPER ADMIN (skip if already present)
    for key in NEW_SUPER_ADMIN_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, role_id, permission_key) "
                "SELECT CAST(:id AS uuid), CAST(:role_id AS uuid), :key "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM permissions WHERE role_id = CAST(:role_id AS uuid) AND permission_key = :key"
                ")"
            ).bindparams(id=str(uuid.uuid4()), role_id=SUPER_ADMIN_ROLE_ID, key=key)
        )

    op.execute(
        sa.text(
            "INSERT INTO roles (id, name, description) VALUES "
            "(CAST(:id AS uuid), 'ADMIN', 'Global operational access without lifecycle/destructive controls')"
            " ON CONFLICT (name) DO NOTHING"
        ).bindparams(id=ADMIN_ROLE_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO roles (id, name, description) VALUES "
            "(CAST(:id AS uuid), 'EVENT COORDINATOR', 'Read-only access to assigned events')"
            " ON CONFLICT (name) DO NOTHING"
        ).bindparams(id=EVENT_COORDINATOR_ROLE_ID)
    )

    for key in ADMIN_ROLE_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, role_id, permission_key) "
                "SELECT CAST(:id AS uuid), CAST(:role_id AS uuid), :key "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM permissions p JOIN roles r ON p.role_id = r.id "
                "  WHERE r.name = 'ADMIN' AND p.permission_key = :key"
                ")"
            ).bindparams(id=str(uuid.uuid4()), role_id=ADMIN_ROLE_ID, key=key)
        )

    for key in EVENT_COORDINATOR_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, role_id, permission_key) "
                "SELECT CAST(:id AS uuid), CAST(:role_id AS uuid), :key "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM permissions p JOIN roles r ON p.role_id = r.id "
                "  WHERE r.name = 'EVENT COORDINATOR' AND p.permission_key = :key"
                ")"
            ).bindparams(id=str(uuid.uuid4()), role_id=EVENT_COORDINATOR_ROLE_ID, key=key)
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM permissions WHERE role_id = CAST(:id AS uuid)").bindparams(id=EVENT_COORDINATOR_ROLE_ID))
    op.execute(sa.text("DELETE FROM permissions WHERE role_id = CAST(:id AS uuid)").bindparams(id=ADMIN_ROLE_ID))
    op.execute(sa.text("DELETE FROM roles WHERE id = CAST(:id AS uuid)").bindparams(id=EVENT_COORDINATOR_ROLE_ID))
    op.execute(sa.text("DELETE FROM roles WHERE id = CAST(:id AS uuid)").bindparams(id=ADMIN_ROLE_ID))
    op.execute(
        sa.text("DELETE FROM permissions WHERE permission_key IN :keys").bindparams(
            keys=tuple(NEW_SUPER_ADMIN_PERMISSIONS)
        )
    )
    op.drop_index("ix_event_admin_assignments_admin_user_id", table_name="event_admin_assignments")
    op.drop_index("ix_event_admin_assignments_event_id", table_name="event_admin_assignments")
    op.drop_table("event_admin_assignments")
