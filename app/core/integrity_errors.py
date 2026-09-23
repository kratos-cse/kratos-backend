"""Map PostgreSQL integrity violations to application errors."""
from sqlalchemy.exc import IntegrityError

from app.core.errors import ALREADY_REGISTERED, AppError

# Partial unique indexes on registrations (see app/models/registration.py).
DUPLICATE_REGISTRATION_CONSTRAINTS = frozenset(
    {
        "uq_registrations_event_profile_active",
        "uq_registrations_event_team_active",
    }
)

# Partial unique indexes on team_members (see app/models/team.py).
DUPLICATE_TEAM_MEMBER_CONSTRAINTS = frozenset(
    {
        "uq_team_members_event_profile_active",
    }
)


def _constraint_name(exc: IntegrityError) -> str | None:
    orig = exc.orig
    if orig is None:
        return None
    name = getattr(orig, "constraint_name", None)
    if name:
        return name
    diag = getattr(orig, "diag", None)
    if diag is not None:
        return getattr(diag, "constraint_name", None)
    return None


def is_duplicate_registration_error(exc: IntegrityError) -> bool:
    name = _constraint_name(exc)
    return name in DUPLICATE_REGISTRATION_CONSTRAINTS if name else False


def is_duplicate_team_member_error(exc: IntegrityError) -> bool:
    name = _constraint_name(exc)
    return name in DUPLICATE_TEAM_MEMBER_CONSTRAINTS if name else False


def raise_duplicate_registration(exc: IntegrityError, message: str) -> None:
    if is_duplicate_registration_error(exc):
        raise AppError(ALREADY_REGISTERED, message, status_code=409) from exc
    raise exc


def raise_duplicate_team_member(exc: IntegrityError, message: str) -> None:
    if is_duplicate_team_member_error(exc):
        raise AppError(ALREADY_REGISTERED, message, status_code=409) from exc
    raise exc
