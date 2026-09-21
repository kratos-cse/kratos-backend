"""Teams invitations + Admin RBAC tables, FK indexes, partial uniques, Super Admin seed.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SUPER_ADMIN_ROLE_ID = "00000000-0000-4000-8000-000000000001"
SUPER_ADMIN_PERMISSIONS = [
    "dashboard",
    "event-management",
    "event-rule-management",
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


def upgrade() -> None:
    # --- Replace full unique with partial unique (preserve LEFT/REMOVED history) ---
    op.drop_constraint("uq_team_members_event_profile", "team_members", type_="unique")
    op.create_index(
        "uq_team_members_event_profile_active",
        "team_members",
        ["event_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('LEFT', 'REMOVED')"),
    )
    op.create_index(
        "uq_team_members_one_active_leader",
        "team_members",
        ["team_id"],
        unique=True,
        postgresql_where=sa.text("role = 'LEADER' AND status NOT IN ('LEFT', 'REMOVED')"),
    )

    # --- FK indexes (0001 missed most of these) ---
    op.create_index("ix_teams_event_id", "teams", ["event_id"])
    op.create_index("ix_teams_leader_profile_id", "teams", ["leader_profile_id"])
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_event_id", "team_members", ["event_id"])
    op.create_index("ix_team_members_profile_id", "team_members", ["profile_id"])
    op.create_index("ix_registrations_event_id", "registrations", ["event_id"])
    op.create_index("ix_registrations_team_id", "registrations", ["team_id"])
    op.create_index("ix_registrations_profile_id", "registrations", ["profile_id"])
    op.create_index("ix_registrations_payment_id", "registrations", ["payment_id"])
    op.create_index("ix_payments_payer_profile_id", "payments", ["payer_profile_id"])
    op.create_index("ix_payments_team_member_id", "payments", ["team_member_id"])
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    # --- team_invitations ---
    op.create_table(
        "team_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_by_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_team_invitations_team_id", "team_invitations", ["team_id"])
    op.create_index("ix_team_invitations_code", "team_invitations", ["code"])

    # --- RBAC ---
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("description", sa.String(), nullable=True),
    )
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("permission_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_permissions_role_id", "permissions", ["role_id"])
    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_admin_users_user_id", "admin_users", ["user_id"])
    op.create_index("ix_admin_users_role_id", "admin_users", ["role_id"])

    # Seed SUPER ADMIN role so bootstrap can grant the first admin_user.
    op.execute(
        sa.text(
            "INSERT INTO roles (id, name, description) VALUES "
            "(:id, 'SUPER ADMIN', 'Full system access — seeded by migration 0003')"
        ).bindparams(id=SUPER_ADMIN_ROLE_ID)
    )
    for key in SUPER_ADMIN_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (id, role_id, permission_key) VALUES "
                "(:id, :role_id, :key)"
            ).bindparams(id=str(uuid.uuid4()), role_id=SUPER_ADMIN_ROLE_ID, key=key)
        )


def downgrade() -> None:
    op.drop_table("admin_users")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_index("ix_team_invitations_code", table_name="team_invitations")
    op.drop_index("ix_team_invitations_team_id", table_name="team_invitations")
    op.drop_table("team_invitations")

    for name in (
        "ix_profiles_user_id",
        "ix_payments_team_member_id",
        "ix_payments_payer_profile_id",
        "ix_registrations_payment_id",
        "ix_registrations_profile_id",
        "ix_registrations_team_id",
        "ix_registrations_event_id",
        "ix_team_members_profile_id",
        "ix_team_members_event_id",
        "ix_team_members_team_id",
        "ix_teams_leader_profile_id",
        "ix_teams_event_id",
    ):
        op.drop_index(name)

    op.drop_index("uq_team_members_one_active_leader", table_name="team_members")
    op.drop_index("uq_team_members_event_profile_active", table_name="team_members")
    op.create_unique_constraint(
        "uq_team_members_event_profile", "team_members", ["event_id", "profile_id"]
    )
