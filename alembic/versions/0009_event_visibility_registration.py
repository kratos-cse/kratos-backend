"""Event visibility + registration_status replace lifecycle status.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23

Migrates:
  status OPEN       -> PUBLISHED + registration OPEN
  status CLOSED/etc -> UNPUBLISHED + registration CLOSED
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    event_visibility = postgresql.ENUM("PUBLISHED", "UNPUBLISHED", name="event_visibility", create_type=False)
    event_registration_status = postgresql.ENUM(
        "OPEN", "CLOSED", name="event_registration_status", create_type=False
    )

    op.execute("CREATE TYPE event_visibility AS ENUM ('PUBLISHED', 'UNPUBLISHED')")
    op.execute("CREATE TYPE event_registration_status AS ENUM ('OPEN', 'CLOSED')")

    op.add_column(
        "events",
        sa.Column(
            "visibility",
            event_visibility,
            nullable=False,
            server_default="UNPUBLISHED",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "registration_status",
            event_registration_status,
            nullable=False,
            server_default="CLOSED",
        ),
    )

    op.execute(
        """
        UPDATE events
        SET visibility = 'PUBLISHED', registration_status = 'OPEN'
        WHERE status = 'OPEN'
        """
    )
    op.execute(
        """
        UPDATE events
        SET visibility = 'UNPUBLISHED', registration_status = 'CLOSED'
        WHERE status IN ('CLOSED', 'COMPLETED', 'CANCELLED')
        """
    )

    op.drop_column("events", "status")
    op.execute("DROP TYPE IF EXISTS event_status")


def downgrade() -> None:
    event_status = postgresql.ENUM("OPEN", "CLOSED", "COMPLETED", "CANCELLED", name="event_status", create_type=False)
    op.execute("CREATE TYPE event_status AS ENUM ('OPEN', 'CLOSED', 'COMPLETED', 'CANCELLED')")
    op.add_column(
        "events",
        sa.Column("status", event_status, nullable=False, server_default="CLOSED"),
    )
    op.execute(
        """
        UPDATE events
        SET status = CASE
            WHEN visibility = 'PUBLISHED' AND registration_status = 'OPEN' THEN 'OPEN'
            ELSE 'CLOSED'
        END::event_status
        """
    )
    op.drop_column("events", "registration_status")
    op.drop_column("events", "visibility")
    op.execute("DROP TYPE IF EXISTS event_registration_status")
    op.execute("DROP TYPE IF EXISTS event_visibility")
