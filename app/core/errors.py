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
PAYMENT_ALREADY_PROCESSED = "PAYMENT_ALREADY_PROCESSED"
INVALID_QR = "INVALID_QR"
DUPLICATE_SCAN = "DUPLICATE_SCAN"
UNAUTHORIZED = "UNAUTHORIZED"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
MEMBER_PAYMENT_NOT_ALLOWED = "MEMBER_PAYMENT_NOT_ALLOWED"
WHATSAPP_UNAVAILABLE = "WHATSAPP_UNAVAILABLE"
INVALID_CATEGORY = "INVALID_CATEGORY"


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
        if "full" in d:
            return TEAM_FULL
        if "already" in d:
            return ALREADY_REGISTERED
        return ALREADY_REGISTERED
    if "not open" in d or "has not opened" in d:
        return REGISTRATION_NOT_OPEN
    if "has closed" in d or "registration has closed" in d:
        return REGISTRATION_CLOSED
    if "capacity" in d or "reached capacity" in d:
        return CAPACITY_FULL
    if "closed" in d and "event" in d:
        return EVENT_CLOSED
    return VALIDATION_ERROR
