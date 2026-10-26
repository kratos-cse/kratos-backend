"""Application errors with stable codes for API clients."""

EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
EVENT_CLOSED = "EVENT_CLOSED"
REGISTRATION_NOT_OPEN = "REGISTRATION_NOT_OPEN"
REGISTRATION_CLOSED = "REGISTRATION_CLOSED"
REGISTRATION_NOT_ALLOWED = "REGISTRATION_NOT_ALLOWED"
CAPACITY_FULL = "CAPACITY_FULL"
TEAM_NOT_FOUND = "TEAM_NOT_FOUND"
TEAM_FULL = "TEAM_FULL"
TEAM_MEMBER_EXISTS = "TEAM_MEMBER_EXISTS"
ALREADY_REGISTERED = "ALREADY_REGISTERED"
ALREADY_MEMBER = "ALREADY_MEMBER"
INVITE_REVOKED = "INVITE_REVOKED"
PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
PAYMENT_FAILED = "PAYMENT_FAILED"
PAYMENT_GATEWAY_UNAVAILABLE = "PAYMENT_GATEWAY_UNAVAILABLE"
INVALID_PAYMENT_SIGNATURE = "INVALID_PAYMENT_SIGNATURE"
PAYMENT_ALREADY_PROCESSED = "PAYMENT_ALREADY_PROCESSED"
PAYMENT_RECONCILIATION_PENDING = "PAYMENT_RECONCILIATION_PENDING"
PAYMENT_ORPHAN_CAPTURED = "PAYMENT_ORPHAN_CAPTURED"
INVALID_QR = "INVALID_QR"
DUPLICATE_SCAN = "DUPLICATE_SCAN"
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
MEMBER_PAYMENT_NOT_ALLOWED = "MEMBER_PAYMENT_NOT_ALLOWED"
WHATSAPP_UNAVAILABLE = "WHATSAPP_UNAVAILABLE"
INVALID_CATEGORY = "INVALID_CATEGORY"
TEAM_REQUIRED_MEMBERS_NOT_MET = "TEAM_REQUIRED_MEMBERS_NOT_MET"
TEAM_SUBSTITUTE_LIMIT = "TEAM_SUBSTITUTE_LIMIT"
TEAM_MANDATORY_FULL = "TEAM_MANDATORY_FULL"
LEADER_ENTRY_NOT_ALLOWED = "LEADER_ENTRY_NOT_ALLOWED"
ROSTER_INVALID = "ROSTER_INVALID"
RECEIPT_DATA_INCOMPLETE = "RECEIPT_DATA_INCOMPLETE"
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
INVALID_FIELD_VALUE = "INVALID_FIELD_VALUE"
UNKNOWN_FIELD = "UNKNOWN_FIELD"
INTERNAL_ERROR = "INTERNAL_ERROR"
CONFLICT = "CONFLICT"

# Participant-facing roster copy (keep in sync with kratos-frontend lib/errors/userMessages.js)
MSG_TEAM_ROSTER_FULL = (
    "This team has reached its maximum size. You cannot add another participant."
)
MSG_ALREADY_ON_TEAM_FOR_EVENT = (
    "That participant is already registered for this event on another team."
)
MSG_DUPLICATE_PHONE_ON_EVENT = (
    "A participant with this phone number is already on a roster for this event."
)
MSG_SUBSTITUTE_SLOTS_FULL = "All substitute slots for this team are filled."
MSG_MANDATORY_ROSTER_FULL = (
    "This team already has the required number of members. "
    "Add substitutes only if substitute slots remain."
)
MSG_ROSTER_INVALID_SUBSTITUTE = (
    "This event uses regular member slots, not substitutes. "
    "Add this participant as a member instead."
)


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def http_code_from_detail(status_code: int, detail: str) -> str:
    """Best-effort map for legacy HTTPException(detail=str) callers."""
    d = (detail or "").lower()
    if status_code == 401:
        return UNAUTHORIZED
    if status_code == 403:
        return FORBIDDEN
    if status_code == 404:
        if "event" in d:
            return EVENT_NOT_FOUND
        if "team" in d:
            return TEAM_NOT_FOUND
        return NOT_FOUND
    if status_code == 409:
        if "maximum size" in d or ("team" in d and "full" in d) or "roster is full" in d:
            return TEAM_FULL
        if "substitute slot" in d or ("substitute" in d and ("filled" in d or "remaining" in d)):
            return TEAM_SUBSTITUTE_LIMIT
        if "mandatory" in d and "full" in d:
            return TEAM_MANDATORY_FULL
        if "member slots" in d or "not substitutes" in d:
            return ROSTER_INVALID
        if "already" in d or "duplicate" in d:
            return ALREADY_REGISTERED
        return CONFLICT
    if "not open" in d or "has not opened" in d:
        return REGISTRATION_NOT_OPEN
    if "has closed" in d or "registration has closed" in d:
        return REGISTRATION_CLOSED
    if "capacity" in d or "reached capacity" in d:
        return CAPACITY_FULL
    if "closed" in d and "event" in d:
        return EVENT_CLOSED
    return VALIDATION_ERROR
