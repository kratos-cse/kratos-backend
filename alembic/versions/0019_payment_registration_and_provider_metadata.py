"""Durable payment→registration link and safe Razorpay failure metadata."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payments",
        sa.Column("registration_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_payments_registration_id",
        "payments",
        "registrations",
        ["registration_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_payments_registration_id", "payments", ["registration_id"])

    op.execute(
        """
        UPDATE payments p
        SET registration_id = r.id
        FROM registrations r
        WHERE r.payment_id = p.id AND p.registration_id IS NULL
        """
    )

    op.add_column("payments", sa.Column("provider_error_code", sa.String(64), nullable=True))
    op.add_column("payments", sa.Column("provider_error_description", sa.String(512), nullable=True))
    op.add_column("payments", sa.Column("provider_error_source", sa.String(64), nullable=True))
    op.add_column("payments", sa.Column("provider_error_step", sa.String(64), nullable=True))
    op.add_column("payments", sa.Column("provider_payment_method", sa.String(64), nullable=True))
    op.add_column(
        "payments",
        sa.Column("last_provider_event_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("payments", "last_provider_event_at")
    op.drop_column("payments", "provider_payment_method")
    op.drop_column("payments", "provider_error_step")
    op.drop_column("payments", "provider_error_source")
    op.drop_column("payments", "provider_error_description")
    op.drop_column("payments", "provider_error_code")
    op.drop_index("ix_payments_registration_id", table_name="payments")
    op.drop_constraint("fk_payments_registration_id", "payments", type_="foreignkey")
    op.drop_column("payments", "registration_id")
