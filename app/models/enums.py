"""Enum values this branch needs. Kept local rather than importing another
branch's app/models/enums.py — see app/models/external_mirrors.py for why.
Values match the finalized DB schema (kratos_database_schema_final.xlsx);
reconcile against whichever branch's copy of this file wins at merge time
if any drift shows up.
"""
import enum


class FeeChargeModel(str, enum.Enum):
    PER_TEAM = "PER_TEAM"
    PER_MEMBER = "PER_MEMBER"


class RegistrationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class TeamStatus(str, enum.Enum):
    FORMING = "FORMING"
    PAID = "PAID"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"


class TeamMemberRole(str, enum.Enum):
    LEADER = "LEADER"
    MEMBER = "MEMBER"


class TeamMemberStatus(str, enum.Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"
    REMOVED = "REMOVED"


class PaymentType(str, enum.Enum):
    TEAM_REGISTRATION = "TEAM_REGISTRATION"
    SOLO_REGISTRATION = "SOLO_REGISTRATION"
    TEAM_MEMBER_TOPUP = "TEAM_MEMBER_TOPUP"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
