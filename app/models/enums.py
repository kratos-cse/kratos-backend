import enum


class EventStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RegistrationAvailability(str, enum.Enum):
    """Authoritative registration UX state — computed server-side only."""

    OPEN = "OPEN"
    EVENT_CLOSED = "EVENT_CLOSED"
    NOT_YET_OPEN = "NOT_YET_OPEN"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    FULL = "FULL"


class EventCategory(str, enum.Enum):
    """Locked KRATOS'26 catalogue categories. PLAYGROUND includes sports."""

    TECHNICAL = "TECHNICAL"
    PLAYGROUND = "PLAYGROUND"
    SPARK = "SPARK"
    ONLINE = "ONLINE"
    CULTURAL = "CULTURAL"


class EventSlot(str, enum.Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"
    FULL_DAY = "FULL_DAY"
    MULTI_DAY = "MULTI_DAY"


class RegistrationMode(str, enum.Enum):
    """Who may register for the event. Fee is always paid once (solo or leader)."""

    INDIVIDUAL_ONLY = "INDIVIDUAL_ONLY"
    TEAM_ONLY = "TEAM_ONLY"
    TEAM_OR_INDIVIDUAL = "TEAM_OR_INDIVIDUAL"


class CapacityType(str, enum.Enum):
    PARTICIPANTS = "PARTICIPANTS"
    TEAMS = "TEAMS"


class MemberRegistrationMode(str, enum.Enum):
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
    SUBSTITUTE = "SUBSTITUTE"


class TeamMemberEntrySource(str, enum.Enum):
    """How the roster seat was created."""

    LINKED_ACCOUNT = "LINKED_ACCOUNT"
    LEADER_ENTERED = "LEADER_ENTERED"


class TeamMemberStatus(str, enum.Enum):
    """PENDING_PAYMENT is only for the leader awaiting TEAM_REGISTRATION payment."""

    PENDING_PAYMENT = "PENDING_PAYMENT"
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"
    REMOVED = "REMOVED"


class RegistrationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class PaymentType(str, enum.Enum):
    """KRATOS'26: only solo participant or team leader pays. No member payments."""

    TEAM_REGISTRATION = "TEAM_REGISTRATION"
    SOLO_REGISTRATION = "SOLO_REGISTRATION"


class PaymentStatus(str, enum.Enum):
    CREATED = "CREATED"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


class NotificationKind(str, enum.Enum):
    PAYMENT_CONFIRMATION = "PAYMENT_CONFIRMATION"
    REGISTRATION_CONFIRMATION = "REGISTRATION_CONFIRMATION"
    MEMBER_JOINED = "MEMBER_JOINED"
    MEMBER_CONFIRMATION = "MEMBER_CONFIRMATION"
    TEAM_COMPLETED = "TEAM_COMPLETED"
    REFUND = "REFUND"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    REMINDER = "REMINDER"


class NotificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class AttendanceScanResult(str, enum.Enum):
    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    INVALID = "INVALID"
    NOT_PAID = "NOT_PAID"


class DuplicateScanBehavior(str, enum.Enum):
    REJECT = "REJECT"
    ACCEPT = "ACCEPT"
    WARN = "WARN"
