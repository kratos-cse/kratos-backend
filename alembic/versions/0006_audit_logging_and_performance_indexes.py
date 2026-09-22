"""Audit logging table and query performance indexes across high-traffic tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create audit_logs table if not exists
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
            actor_role VARCHAR(50) NOT NULL DEFAULT 'USER',
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50) NOT NULL,
            resource_id VARCHAR(100),
            status VARCHAR(50) NOT NULL DEFAULT 'SUCCESS',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            ip_address VARCHAR(100),
            user_agent VARCHAR(500)
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_user_id ON audit_logs (actor_user_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_profile_id ON audit_logs (actor_profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_actor_role ON audit_logs (actor_role);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs (action);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_resource_type ON audit_logs (resource_type);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_resource_id ON audit_logs (resource_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_status ON audit_logs (status);")

    # 2. Add performance indexes on registrations
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_event_id ON registrations (event_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_profile_id ON registrations (profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_team_id ON registrations (team_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_payment_id ON registrations (payment_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_status ON registrations (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_registrations_created_at ON registrations (created_at);")

    # 3. Add performance indexes on payments
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_payer_profile_id ON payments (payer_profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_status ON payments (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_payment_type ON payments (payment_type);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payments_created_at ON payments (created_at);")

    # 4. Add performance indexes on teams
    op.execute("CREATE INDEX IF NOT EXISTS ix_teams_event_id ON teams (event_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_teams_leader_profile_id ON teams (leader_profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_teams_status ON teams (status);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_teams_created_at ON teams (created_at);")

    # 5. Add performance indexes on team_members
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_members_team_id ON team_members (team_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_members_event_id ON team_members (event_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_members_profile_id ON team_members (profile_id);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_team_members_status ON team_members (status);")

    # 6. Add performance indexes on profiles
    op.execute("CREATE INDEX IF NOT EXISTS ix_profiles_full_name ON profiles (full_name);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_profiles_contact_email ON profiles (contact_email);")
    op.execute("CREATE INDEX IF NOT EXISTS ix_profiles_phone ON profiles (phone);")


def downgrade() -> None:
    op.drop_index("ix_profiles_phone", table_name="profiles")
    op.drop_index("ix_profiles_contact_email", table_name="profiles")
    op.drop_index("ix_profiles_full_name", table_name="profiles")

    op.drop_index("ix_team_members_status", table_name="team_members")
    op.drop_index("ix_team_members_profile_id", table_name="team_members")
    op.drop_index("ix_team_members_event_id", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")

    op.drop_index("ix_teams_created_at", table_name="teams")
    op.drop_index("ix_teams_status", table_name="teams")
    op.drop_index("ix_teams_leader_profile_id", table_name="teams")
    op.drop_index("ix_teams_event_id", table_name="teams")

    op.drop_index("ix_payments_created_at", table_name="payments")
    op.drop_index("ix_payments_payment_type", table_name="payments")
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_index("ix_payments_payer_profile_id", table_name="payments")

    op.drop_index("ix_registrations_created_at", table_name="registrations")
    op.drop_index("ix_registrations_status", table_name="registrations")
    op.drop_index("ix_registrations_payment_id", table_name="registrations")
    op.drop_index("ix_registrations_team_id", table_name="registrations")
    op.drop_index("ix_registrations_profile_id", table_name="registrations")
    op.drop_index("ix_registrations_event_id", table_name="registrations")

    op.drop_table("audit_logs")
