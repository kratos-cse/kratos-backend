"""Create HTF submissions, screening results, and payments tables (Person 3).

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create htf_submissions table
    op.create_table(
        "htf_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("htf_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("google_drive_file_id", sa.String(255), nullable=False),
        sa.Column("google_drive_folder_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SUBMITTED"),
        sa.Column(
            "uploaded_by_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("application_id", "version", name="uq_htf_submissions_app_version"),
        sa.CheckConstraint("status IN ('SUBMITTED', 'REPLACED')", name="ck_htf_submissions_status"),
    )
    op.create_index("ix_htf_submissions_app_id", "htf_submissions", ["application_id"])
    op.create_index("ix_htf_submissions_status", "htf_submissions", ["status"])
    op.create_index("ix_htf_submissions_drive_file_id", "htf_submissions", ["google_drive_file_id"])
    op.create_index(
        "uq_htf_submissions_active",
        "htf_submissions",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SUBMITTED'"),
    )

    # 2. Create htf_screening_results table
    op.create_table(
        "htf_screening_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("htf_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_admin_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("application_id", name="uq_htf_screening_results_app_id"),
        sa.CheckConstraint("result IN ('SHORTLISTED', 'NOT_SHORTLISTED')", name="ck_htf_screening_result"),
    )
    op.create_index("ix_htf_screening_results_result", "htf_screening_results", ["result"])

    # 3. Create htf_payments table
    op.create_table(
        "htf_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("htf_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "payer_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("razorpay_order_id", sa.String(255), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(255), nullable=True, unique=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("status", sa.String(32), nullable=False, server_default="CREATED"),
        sa.Column("refund_id", sa.String(255), nullable=True),
        sa.Column("refund_amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('CREATED', 'PAID', 'FAILED', 'REFUNDED')",
            name="ck_htf_payments_status",
        ),
    )
    op.create_index("ix_htf_payments_app_id", "htf_payments", ["application_id"])
    op.create_index("ix_htf_payments_status", "htf_payments", ["status"])
    op.create_index(
        "uq_htf_payments_live",
        "htf_payments",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('CREATED', 'PAID')"),
    )


def downgrade() -> None:
    op.drop_table("htf_payments")
    op.drop_table("htf_screening_results")
    op.drop_table("htf_submissions")
