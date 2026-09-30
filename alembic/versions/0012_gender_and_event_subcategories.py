"""Add gender, subcategory, and gender_category to profiles, team_members, events, and event_registration_rules.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_GENDERS = ["MALE", "FEMALE", "OTHER"]
_GENDER_CATEGORIES = ["OPEN", "MALE_ONLY", "FEMALE_ONLY", "MIXED"]


def upgrade() -> None:
    bind = op.get_bind()

    gender_enum = postgresql.ENUM(*_GENDERS, name="gender", create_type=False)
    gender_category_enum = postgresql.ENUM(*_GENDER_CATEGORIES, name="gender_category", create_type=False)

    gender_enum.create(bind, checkfirst=True)
    gender_category_enum.create(bind, checkfirst=True)

    # 1. Add gender to profiles
    op.add_column("profiles", sa.Column("gender", gender_enum, nullable=True))

    # 2. Add gender to team_members
    op.add_column("team_members", sa.Column("gender", gender_enum, nullable=True))

    # 3. Add subcategory and gender_category to events
    op.add_column("events", sa.Column("subcategory", sa.String(100), nullable=True))
    op.add_column(
        "events",
        sa.Column("gender_category", gender_category_enum, nullable=False, server_default="OPEN"),
    )

    # 4. Add gender_category to event_registration_rules
    op.add_column(
        "event_registration_rules",
        sa.Column("gender_category", gender_category_enum, nullable=False, server_default="OPEN"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_column("event_registration_rules", "gender_category")
    op.drop_column("events", "gender_category")
    op.drop_column("events", "subcategory")
    op.drop_column("team_members", "gender")
    op.drop_column("profiles", "gender")

    gender_category_enum = postgresql.ENUM(*_GENDER_CATEGORIES, name="gender_category", create_type=False)
    gender_enum = postgresql.ENUM(*_GENDERS, name="gender", create_type=False)

    gender_category_enum.drop(bind, checkfirst=True)
    gender_enum.drop(bind, checkfirst=True)
