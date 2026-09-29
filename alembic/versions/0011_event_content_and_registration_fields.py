"""Event content sections, coordinators, registration fields, and responses.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_CONTENT_SECTION_TYPES = [
    "REQUIREMENTS",
    "RULES",
    "ELIGIBILITY",
    "PRIZES",
    "INSTRUCTIONS",
    "WHAT_TO_BRING",
    "FORMAT",
    "JUDGING_CRITERIA",
    "CUSTOM",
]
_REGISTRATION_FIELD_SCOPES = ["REGISTRATION", "TEAM_MEMBER"]
_REGISTRATION_FIELD_TYPES = [
    "TEXT",
    "TEXTAREA",
    "NUMBER",
    "EMAIL",
    "PHONE",
    "DATE",
    "MCQ",
    "SINGLE_SELECT",
    "MULTI_SELECT",
    "CHECKBOX",
]
_REGISTRATION_FIELD_SOURCES = ["CUSTOM", "PROFILE"]


def upgrade() -> None:
    bind = op.get_bind()

    content_section_type = postgresql.ENUM(
        *_CONTENT_SECTION_TYPES, name="content_section_type", create_type=False
    )
    registration_field_scope = postgresql.ENUM(
        *_REGISTRATION_FIELD_SCOPES, name="registration_field_scope", create_type=False
    )
    registration_field_type = postgresql.ENUM(
        *_REGISTRATION_FIELD_TYPES, name="registration_field_type", create_type=False
    )
    registration_field_source = postgresql.ENUM(
        *_REGISTRATION_FIELD_SOURCES, name="registration_field_source", create_type=False
    )

    for enum_type in (
        content_section_type,
        registration_field_scope,
        registration_field_type,
        registration_field_source,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "event_content_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("section_type", content_section_type, nullable=False, server_default="CUSTOM"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_content_sections_event_id", "event_content_sections", ["event_id"])

    op.create_table(
        "event_coordinators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contact", sa.String(255), nullable=False),
        sa.Column("role", sa.String(200), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_event_coordinators_event_id", "event_coordinators", ["event_id"])

    op.create_table(
        "event_registration_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("scope", registration_field_scope, nullable=False),
        sa.Column("field_key", sa.String(100), nullable=False),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("field_type", registration_field_type, nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("placeholder", sa.String(300), nullable=True),
        sa.Column("help_text", sa.String(500), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("source", registration_field_source, nullable=False, server_default="CUSTOM"),
        sa.Column("profile_field_key", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "scope", "field_key", name="uq_event_registration_fields_event_scope_key"),
    )
    op.create_index("ix_event_registration_fields_event_id", "event_registration_fields", ["event_id"])

    op.create_table(
        "registration_field_responses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("event_registration_fields.id"), nullable=False),
        sa.Column("registration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("registrations.id"), nullable=True),
        sa.Column("team_member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "(registration_id IS NOT NULL AND team_member_id IS NULL) OR "
            "(registration_id IS NULL AND team_member_id IS NOT NULL)",
            name="ck_registration_field_responses_target",
        ),
        sa.UniqueConstraint("field_id", "registration_id", name="uq_field_response_registration"),
        sa.UniqueConstraint("field_id", "team_member_id", name="uq_field_response_team_member"),
    )
    op.create_index(
        "ix_registration_field_responses_registration_id",
        "registration_field_responses",
        ["registration_id"],
    )
    op.create_index(
        "ix_registration_field_responses_team_member_id",
        "registration_field_responses",
        ["team_member_id"],
    )

    op.execute(
        """
        INSERT INTO event_coordinators (id, event_id, name, contact, display_order)
        SELECT gen_random_uuid(), id,
               COALESCE(NULLIF(TRIM(coordinator), ''), 'Coordinator'),
               COALESCE(NULLIF(TRIM(coord_contact), ''), 'Contact'),
               0
        FROM events
        WHERE (coordinator IS NOT NULL AND TRIM(coordinator) != '')
           OR (coord_contact IS NOT NULL AND TRIM(coord_contact) != '')
        """
    )


def downgrade() -> None:
    op.drop_index("ix_registration_field_responses_team_member_id", table_name="registration_field_responses")
    op.drop_index("ix_registration_field_responses_registration_id", table_name="registration_field_responses")
    op.drop_table("registration_field_responses")
    op.drop_index("ix_event_registration_fields_event_id", table_name="event_registration_fields")
    op.drop_table("event_registration_fields")
    op.drop_index("ix_event_coordinators_event_id", table_name="event_coordinators")
    op.drop_table("event_coordinators")
    op.drop_index("ix_event_content_sections_event_id", table_name="event_content_sections")
    op.drop_table("event_content_sections")

    bind = op.get_bind()
    for name in (
        "registration_field_source",
        "registration_field_type",
        "registration_field_scope",
        "content_section_type",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
