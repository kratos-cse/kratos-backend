"""Replace fee_charge_model with registration_mode on event rules.

KRATOS'26: fee is always one charge (solo or team leader). PER_MEMBER /
TEAM_MEMBER_TOPUP are retired in application code; payment_type enum value
TEAM_MEMBER_TOPUP is left in Postgres for existing rows.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_REGISTRATION_MODE_VALUES = ["INDIVIDUAL_ONLY", "TEAM_ONLY", "TEAM_OR_INDIVIDUAL"]


def upgrade() -> None:
    bind = op.get_bind()
    registration_mode = postgresql.ENUM(*_REGISTRATION_MODE_VALUES, name="registration_mode")
    registration_mode.create(bind, checkfirst=True)

    op.add_column(
        "event_registration_rules",
        sa.Column("registration_mode", registration_mode, nullable=True),
    )

    op.execute(
        """
        UPDATE event_registration_rules
        SET registration_mode = CASE
            WHEN allow_individual = false THEN 'TEAM_ONLY'::registration_mode
            WHEN team_max_size = 1 AND team_min_size = 1 THEN 'INDIVIDUAL_ONLY'::registration_mode
            ELSE 'TEAM_OR_INDIVIDUAL'::registration_mode
        END
        """
    )

    op.alter_column(
        "event_registration_rules",
        "registration_mode",
        nullable=False,
        server_default="TEAM_OR_INDIVIDUAL",
    )

    op.drop_column("event_registration_rules", "fee_charge_model")


def downgrade() -> None:
    bind = op.get_bind()
    fee_charge_model = postgresql.ENUM("PER_TEAM", "PER_MEMBER", name="fee_charge_model", create_type=False)
    fee_charge_model.create(bind, checkfirst=True)

    op.add_column(
        "event_registration_rules",
        sa.Column(
            "fee_charge_model",
            fee_charge_model,
            nullable=False,
            server_default="PER_TEAM",
        ),
    )

    op.drop_column("event_registration_rules", "registration_mode")

    postgresql.ENUM(name="registration_mode").drop(bind, checkfirst=True)
