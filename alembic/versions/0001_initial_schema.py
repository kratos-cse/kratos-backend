"""Initial schema — Authentication / User Profile / Events / Registration slice

Creates the 10 tables this branch's endpoints touch: users, profiles,
events, event_registration_rules, teams, team_members, payments (minimal
mirror), registrations, receipts, qr_codes. Tables owned by other feature
branches (team_invitations, attendance_*, notifications, admin_users,
roles, permissions) are intentionally NOT created here to avoid stepping
on their migrations — see README.md.

Revision ID: 0001
Revises:
Create Date: 2026-09-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_ENUMS = {
    "event_status": ["OPEN", "CLOSED", "COMPLETED", "CANCELLED"],
    "fee_charge_model": ["PER_TEAM", "PER_MEMBER"],
    "capacity_type": ["PARTICIPANTS", "TEAMS"],
    "member_registration_mode": ["LEADER_MANAGED", "SELF_ENTRY"],
    "team_status": ["FORMING", "PAID", "COMPLETE", "CANCELLED"],
    "team_member_role": ["LEADER", "MEMBER"],
    "team_member_status": ["PENDING_PAYMENT", "ACTIVE", "LEFT", "REMOVED"],
    "registration_status": ["PENDING", "CONFIRMED", "CANCELLED"],
    "payment_type": ["TEAM_REGISTRATION", "SOLO_REGISTRATION", "TEAM_MEMBER_TOPUP"],
    "payment_status": ["CREATED", "PAID", "FAILED", "REFUNDED"],
}


def upgrade() -> None:
    bind = op.get_bind()
    enum_types = {}
    for name, values in _ENUMS.items():
        enum_type = postgresql.ENUM(*values, name=name, create_type=False)
        enum_type.create(bind, checkfirst=True)
        enum_types[name] = enum_type

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("google_sub", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("is_admin_flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("contact_email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("college_name", sa.String(), nullable=True),
        sa.Column("department", sa.String(), nullable=True),
        sa.Column("year_of_study", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("short_desc", sa.Text(), nullable=True),
        sa.Column("long_desc", sa.Text(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("coordinator", sa.String(), nullable=True),
        sa.Column("coord_contact", sa.String(), nullable=True),
        sa.Column("fee", sa.Numeric(10, 2), nullable=True),
        sa.Column("venue", sa.String(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("whatsapp_group_link", sa.String(), nullable=True),
        sa.Column("google_sheet_id", sa.String(), nullable=True),
        sa.Column("google_sheet_url", sa.String(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", enum_types["event_status"], nullable=False, server_default="OPEN"),
    )

    op.create_table(
        "event_registration_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False, unique=True),
        sa.Column("team_min_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("team_max_size", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("allow_individual", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("fee_charge_model", enum_types["fee_charge_model"], nullable=False, server_default="PER_TEAM"),
        sa.Column("allow_team_invite_flow", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("requires_qr_checkin", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("capacity_type", enum_types["capacity_type"], nullable=False, server_default="PARTICIPANTS"),
        sa.Column(
            "member_registration_mode",
            enum_types["member_registration_mode"],
            nullable=False,
            server_default="LEADER_MANAGED",
        ),
        sa.Column("custom_fields", postgresql.JSONB(), nullable=True),
        sa.Column("registration_opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("registration_closes_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("leader_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("status", enum_types["team_status"], nullable=False, server_default="FORMING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("role", enum_types["team_member_role"], nullable=False),
        sa.Column("status", enum_types["team_member_status"], nullable=False, server_default="PENDING_PAYMENT"),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("event_id", "profile_id", name="uq_team_members_event_profile"),
    )

    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payer_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=False),
        sa.Column("payment_type", enum_types["payment_type"], nullable=False),
        sa.Column("team_member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_members.id"), nullable=True),
        sa.Column("razorpay_order_id", sa.String(), nullable=False, unique=True),
        sa.Column("razorpay_payment_id", sa.String(), nullable=True, unique=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="INR"),
        sa.Column("status", enum_types["payment_status"], nullable=False, server_default="CREATED"),
        sa.Column("refund_id", sa.String(), nullable=True),
        sa.Column("refund_amount_paise", sa.BigInteger(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "registrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("profiles.id"), nullable=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("status", enum_types["registration_status"], nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "(team_id IS NOT NULL AND profile_id IS NULL) OR (team_id IS NULL AND profile_id IS NOT NULL)",
            name="ck_registrations_team_xor_profile",
        ),
    )

    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("payments.id"), nullable=False, unique=True),
        sa.Column("receipt_number", sa.String(), nullable=False, unique=True),
        sa.Column("pdf_url", sa.String(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "qr_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("team_members.id"), nullable=True, unique=True
        ),
        sa.Column(
            "registration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("registrations.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "(team_member_id IS NOT NULL AND registration_id IS NULL) OR "
            "(team_member_id IS NULL AND registration_id IS NOT NULL)",
            name="ck_qr_codes_owner_xor",
        ),
    )


def downgrade() -> None:
    op.drop_table("qr_codes")
    op.drop_table("receipts")
    op.drop_table("registrations")
    op.drop_table("payments")
    op.drop_table("team_members")
    op.drop_table("teams")
    op.drop_table("event_registration_rules")
    op.drop_table("events")
    op.drop_table("profiles")
    op.drop_table("users")

    bind = op.get_bind()
    for name in reversed(list(_ENUMS.keys())):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)