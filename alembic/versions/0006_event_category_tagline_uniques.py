"""EVENT hardening: category enum, tagline, starts_at index, active registration uniques.

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

EVENT_CATEGORIES = ("TECHNICAL", "PLAYGROUND", "SPARK", "ONLINE", "CULTURAL")


def upgrade() -> None:
    event_category = postgresql.ENUM(*EVENT_CATEGORIES, name="event_category", create_type=False)
    event_category.create(op.get_bind(), checkfirst=True)

    # Normalize free-form category strings before enum cast (do not drop unknown silently —
    # map known aliases; set unrecognized non-null values to NULL so history is not invented).
    op.execute(
        sa.text(
            """
            UPDATE events SET category = UPPER(TRIM(category))
            WHERE category IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE events SET category = 'PLAYGROUND'
            WHERE category IN ('SPORTS', 'SPORT', 'PLAY GROUND', 'PLAY-GROUND')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE events SET category = NULL
            WHERE category IS NOT NULL
              AND category NOT IN ('TECHNICAL', 'PLAYGROUND', 'SPARK', 'ONLINE', 'CULTURAL')
            """
        )
    )

    op.add_column("events", sa.Column("tagline", sa.String(length=300), nullable=True))

    op.alter_column(
        "events",
        "category",
        existing_type=sa.String(),
        type_=event_category,
        existing_nullable=True,
        postgresql_using="category::event_category",
    )

    op.create_index("ix_events_starts_at", "events", ["starts_at"])

    op.create_index(
        "uq_registrations_event_profile_active",
        "registrations",
        ["event_id", "profile_id"],
        unique=True,
        postgresql_where=sa.text("profile_id IS NOT NULL AND status <> 'CANCELLED'"),
    )
    op.create_index(
        "uq_registrations_event_team_active",
        "registrations",
        ["event_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("team_id IS NOT NULL AND status <> 'CANCELLED'"),
    )


def downgrade() -> None:
    op.drop_index("uq_registrations_event_team_active", table_name="registrations")
    op.drop_index("uq_registrations_event_profile_active", table_name="registrations")
    op.drop_index("ix_events_starts_at", table_name="events")

    op.alter_column(
        "events",
        "category",
        existing_type=postgresql.ENUM(*EVENT_CATEGORIES, name="event_category"),
        type_=sa.String(),
        existing_nullable=True,
        postgresql_using="category::text",
    )
    op.drop_column("events", "tagline")
    postgresql.ENUM(name="event_category").drop(op.get_bind(), checkfirst=True)
