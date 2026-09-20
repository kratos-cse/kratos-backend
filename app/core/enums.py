"""
Shared enums for the Teams module.
If another module (e.g. Events) already defines a shared enums.py, must merge
this content into it instead of keeping two files.
"""
import enum


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


class FeeChargeModel(str, enum.Enum):
    """Defined here only because the Teams service reads it. The real
    EventRegistrationRules model (owned by the Events module) should be
    the source of truth once merged."""
    PER_TEAM = "PER_TEAM"
    PER_MEMBER = "PER_MEMBER"