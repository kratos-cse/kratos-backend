"""HTF 2026 – Problem Statements catalogue and Team metadata.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-02

Two new tables:
  htf_problem_statements  – admin-seeded PS list with domain mapping
  htf_team_meta           – per-team HTF lifecycle state + PS selection
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Enums ─────────────────────────────────────────────────────────────
    htf_problem_domain = postgresql.ENUM(
        "AI_ML",
        "WEB_DEV",
        "CYBER_SECURITY",
        "IOT_EMBEDDED",
        "BLOCKCHAIN",
        "OPEN_INNOVATION",
        name="htf_problem_domain",
        create_type=True,
    )
    htf_problem_domain.create(op.get_bind(), checkfirst=True)

    htf_application_status = postgresql.ENUM(
        "DRAFT",
        "SUBMITTED",
        "SHORTLISTED",
        "NOT_SHORTLISTED",
        "CONFIRMED",
        "WITHDRAWN",
        name="htf_application_status",
        create_type=True,
    )
    htf_application_status.create(op.get_bind(), checkfirst=True)

    # ── 2. htf_problem_statements ────────────────────────────────────────────
    op.create_table(
        "htf_problem_statements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "domain",
            sa.Enum(
                "AI_ML",
                "WEB_DEV",
                "CYBER_SECURITY",
                "IOT_EMBEDDED",
                "BLOCKCHAIN",
                "OPEN_INNOVATION",
                name="htf_problem_domain",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "unique_claim",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("event_id", "code", name="uq_htf_ps_event_code"),
    )
    op.create_index("ix_htf_ps_event_id", "htf_problem_statements", ["event_id"])

    # ── 3. htf_team_meta ─────────────────────────────────────────────────────
    op.create_table(
        "htf_team_meta",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("events.id"),
            nullable=False,
        ),
        sa.Column(
            "ps_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("htf_problem_statements.id"),
            nullable=True,
        ),
        sa.Column(
            "application_status",
            sa.Enum(
                "DRAFT",
                "SUBMITTED",
                "SHORTLISTED",
                "NOT_SHORTLISTED",
                "CONFIRMED",
                "WITHDRAWN",
                name="htf_application_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "roster_locked",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "payment_deadline",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_htf_team_meta_event_id", "htf_team_meta", ["event_id"])
    op.create_index(
        "ix_htf_team_meta_application_status",
        "htf_team_meta",
        ["application_status"],
    )


def downgrade() -> None:
    # Drop in reverse order of creation
    op.drop_index("ix_htf_team_meta_application_status", table_name="htf_team_meta")
    op.drop_index("ix_htf_team_meta_event_id", table_name="htf_team_meta")
    op.drop_table("htf_team_meta")

    op.drop_index("ix_htf_ps_event_id", table_name="htf_problem_statements")
    op.drop_table("htf_problem_statements")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS htf_application_status")
    op.execute("DROP TYPE IF EXISTS htf_problem_domain")
