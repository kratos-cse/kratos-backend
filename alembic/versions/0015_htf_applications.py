"""Add HTF Applications table and status enum.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_HTF_APPLICATION_STATUSES = [
    "DRAFT",
    "SUBMITTED",
    "PPT_PENDING",
    "PPT_SUBMITTED",
    "UNDER_SCREENING",
    "SHORTLISTED",
    "NOT_SHORTLISTED",
    "PAYMENT_PENDING",
    "CONFIRMED",
    "WITHDRAWN",
]


def upgrade() -> None:
    bind = op.get_bind()
    htf_application_status = postgresql.ENUM(
        *_HTF_APPLICATION_STATUSES, name="htf_application_status", create_type=False
    )
    htf_application_status.create(bind, checkfirst=True)

    op.create_table(
        "htf_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("status", htf_application_status, nullable=False, server_default="DRAFT"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_data", postgresql.JSONB(), nullable=True),
        sa.Column("created_by_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "team_id", name="uq_htf_applications_event_team"),
    )
    op.create_index("ix_htf_applications_event_id", "htf_applications", ["event_id"])
    op.create_index("ix_htf_applications_team_id", "htf_applications", ["team_id"])
    op.create_index("ix_htf_applications_status", "htf_applications", ["status"])
    op.create_index("ix_htf_applications_submitted_at", "htf_applications", ["submitted_at"])


def downgrade() -> None:
    op.drop_index("ix_htf_applications_submitted_at", table_name="htf_applications")
    op.drop_index("ix_htf_applications_status", table_name="htf_applications")
    op.drop_index("ix_htf_applications_team_id", table_name="htf_applications")
    op.drop_index("ix_htf_applications_event_id", table_name="htf_applications")
    op.drop_table("htf_applications")

    bind = op.get_bind()
    postgresql.ENUM(name="htf_application_status").drop(bind, checkfirst=True)
