import enum


class EventStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class FeeChargeModel(str, enum.Enum):
    PER_TEAM = "PER_TEAM"
    PER_MEMBER = "PER_MEMBER"


class CapacityType(str, enum.Enum):
    PARTICIPANTS = "PARTICIPANTS"
    TEAMS = "TEAMS"


class MemberRegistrationMode(str, enum.Enum):
    """
    The schema marks this column as an admin-configurable ENUM without
    listing concrete values. Modeled from the shared workflow diagram's two
    flows: the leader filling in every member's details up front, vs.
    members joining via invite link and entering their own.

    ASSUMPTION — confirm the exact value names with the team before relying
    on them elsewhere (e.g. Admin event-rule-management, section 15).
    """
    LEADER_MANAGED = "LEADER_MANAGED"
    SELF_ENTRY = "SELF_ENTRY"


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


class RegistrationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class PaymentType(str, enum.Enum):
    TEAM_REGISTRATION = "TEAM_REGISTRATION"
    SOLO_REGISTRATION = "SOLO_REGISTRATION"
    TEAM_MEMBER_TOPUP = "TEAM_MEMBER_TOPUP"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
