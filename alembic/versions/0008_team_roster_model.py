"""Team roster model: required members + substitutes, SUBSTITUTE role, leader-entered members.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

Backfill:
  required_member_count = team_min_size
  substitute_count = max(0, team_max_size - team_min_size)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enum: SUBSTITUTE on team_member_role ---
    op.execute("ALTER TYPE team_member_role ADD VALUE IF NOT EXISTS 'SUBSTITUTE'")

    team_member_entry_source = postgresql.ENUM(
        "LINKED_ACCOUNT",
        "LEADER_ENTERED",
        name="team_member_entry_source",
        create_type=False,
    )
    team_member_entry_source.create(op.get_bind(), checkfirst=True)

    # --- Event registration rules: roster fields ---
    op.add_column(
        "event_registration_rules",
        sa.Column("required_member_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "event_registration_rules",
        sa.Column("substitute_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        """
        UPDATE event_registration_rules
        SET required_member_count = team_min_size,
            substitute_count = GREATEST(0, team_max_size - team_min_size)
        """
    )

    # --- Team members: nullable profile + leader-entered details ---
    op.alter_column(
        "team_members",
        "profile_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column(
        "team_members",
        sa.Column(
            "entry_source",
            postgresql.ENUM(
                "LINKED_ACCOUNT",
                "LEADER_ENTERED",
                name="team_member_entry_source",
                create_type=False,
            ),
            nullable=False,
            server_default="LINKED_ACCOUNT",
        ),
    )
    op.add_column("team_members", sa.Column("full_name", sa.String(200), nullable=True))
    op.add_column("team_members", sa.Column("phone", sa.String(20), nullable=True))
    op.add_column("team_members", sa.Column("contact_email", sa.String(255), nullable=True))
    op.add_column("team_members", sa.Column("college_name", sa.String(200), nullable=True))
    op.add_column("team_members", sa.Column("year_of_study", sa.String(50), nullable=True))

    # Recreate active profile uniqueness so multiple NULL profile_ids are allowed
    op.drop_index("uq_team_members_event_profile_active", table_name="team_members")
    op.create_index(
        "uq_team_members_event_profile_active",
        "team_members",
        ["event_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text(
            "profile_id IS NOT NULL AND status NOT IN ('LEFT', 'REMOVED')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_team_members_event_profile_active", table_name="team_members")
    op.create_index(
        "uq_team_members_event_profile_active",
        "team_members",
        ["event_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('LEFT', 'REMOVED')"),
    )

    op.drop_column("team_members", "year_of_study")
    op.drop_column("team_members", "college_name")
    op.drop_column("team_members", "contact_email")
    op.drop_column("team_members", "phone")
    op.drop_column("team_members", "full_name")
    op.drop_column("team_members", "entry_source")
    op.alter_column(
        "team_members",
        "profile_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_column("event_registration_rules", "substitute_count")
    op.drop_column("event_registration_rules", "required_member_count")

    op.execute("DROP TYPE IF EXISTS team_member_entry_source")
    # Cannot remove SUBSTITUTE from enum safely in PostgreSQL without recreate
