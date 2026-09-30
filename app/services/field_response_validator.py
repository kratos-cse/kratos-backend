"""Validate and persist custom registration field responses."""
import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    INVALID_FIELD_VALUE,
    MISSING_REQUIRED_FIELD,
    UNKNOWN_FIELD,
    AppError,
)
from app.models.enums import RegistrationFieldScope, RegistrationFieldSource, RegistrationFieldType
from app.models.event_content import EventRegistrationField, RegistrationFieldResponse
from app.models.profile import Profile
from app.models.team import TeamMember
from app.schemas.event_content import FieldResponseInput

PROFILE_FIELD_KEYS = frozenset(
    {"full_name", "contact_email", "phone", "college_name", "department", "year_of_study", "gender"}
)


def _field_options(field: EventRegistrationField) -> list[str]:
    raw = field.options
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict):
        choices = raw.get("choices") or raw.get("options") or []
        return [str(x) for x in choices]
    return []


def _normalize_extracted(val: Any) -> Any:
    if hasattr(val, "value"):
        return val.value
    return val


def _profile_value(profile: Profile, key: str) -> Any:
    val = getattr(profile, key, None)
    return _normalize_extracted(val)


def _team_member_value(member: TeamMember | None, profile: Profile | None, key: str) -> Any:
    if member is not None:
        val = getattr(member, key, None)
        if val is not None and str(val).strip():
            return _normalize_extracted(val)
    if profile is not None:
        return _profile_value(profile, key)
    return None


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _validate_value_type(field: EventRegistrationField, value: Any) -> Any:
    ft = field.field_type
    if ft == RegistrationFieldType.CHECKBOX:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    if ft == RegistrationFieldType.NUMBER:
        try:
            if isinstance(value, (int, float)):
                return value
            return float(value)
        except (TypeError, ValueError):
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Field '{field.label}' must be a number",
                status_code=400,
            )

    if ft == RegistrationFieldType.MULTI_SELECT:
        if not isinstance(value, list):
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Field '{field.label}' must be a list of choices",
                status_code=400,
            )
        choices = _field_options(field)
        invalid = [v for v in value if str(v) not in choices]
        if choices and invalid:
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Invalid choice(s) for '{field.label}'",
                status_code=400,
            )
        return [str(v) for v in value]

    if ft in (RegistrationFieldType.SINGLE_SELECT, RegistrationFieldType.MCQ):
        choices = _field_options(field)
        sval = str(value)
        if choices and sval not in choices:
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Invalid choice for '{field.label}'",
                status_code=400,
            )
        return sval

    if ft == RegistrationFieldType.EMAIL:
        sval = str(value).strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", sval):
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Field '{field.label}' must be a valid email",
                status_code=400,
            )
        return sval

    if ft == RegistrationFieldType.PHONE:
        return str(value).strip()

    if ft == RegistrationFieldType.DATE:
        if isinstance(value, date):
            return value.isoformat()
        sval = str(value).strip()
        try:
            datetime.fromisoformat(sval.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(
                INVALID_FIELD_VALUE,
                f"Field '{field.label}' must be a valid date",
                status_code=400,
            )
        return sval

    return str(value).strip() if isinstance(value, str) else value


async def load_visible_fields(
    db: AsyncSession,
    event_id: uuid.UUID,
    scope: RegistrationFieldScope,
) -> list[EventRegistrationField]:
    result = await db.execute(
        select(EventRegistrationField)
        .where(
            EventRegistrationField.event_id == event_id,
            EventRegistrationField.scope == scope,
            EventRegistrationField.is_visible.is_(True),
        )
        .order_by(EventRegistrationField.display_order)
    )
    return list(result.scalars().all())


async def validate_and_prepare_responses(
    db: AsyncSession,
    event_id: uuid.UUID,
    scope: RegistrationFieldScope,
    responses: list[FieldResponseInput],
    *,
    profile: Profile | None = None,
    team_member: TeamMember | None = None,
    member_profile: Profile | None = None,
) -> list[tuple[EventRegistrationField, Any]]:
    """Validate field responses; return (field, normalized_value) pairs for CUSTOM fields to persist."""
    fields = await load_visible_fields(db, event_id, scope)
    field_by_id = {f.id: f for f in fields}
    submitted = {r.field_id: r.value for r in responses}

    for field_id in submitted:
        if field_id not in field_by_id:
            raise AppError(UNKNOWN_FIELD, f"Unknown field id: {field_id}", status_code=400)

    to_persist: list[tuple[EventRegistrationField, Any]] = []

    for field in fields:
        if field.source == RegistrationFieldSource.PROFILE:
            key = field.profile_field_key or ""
            if key not in PROFILE_FIELD_KEYS:
                raise AppError(
                    INVALID_FIELD_VALUE,
                    f"Invalid profile field key for '{field.label}'",
                    status_code=400,
                )
            if scope == RegistrationFieldScope.REGISTRATION:
                val = _profile_value(profile, key) if profile else None
            else:
                val = _team_member_value(team_member, member_profile, key)
            if field.required and _is_empty(val):
                raise AppError(
                    MISSING_REQUIRED_FIELD,
                    f"Required field '{field.label}' is missing",
                    status_code=400,
                )
            continue

        raw = submitted.get(field.id)
        if _is_empty(raw):
            if field.required:
                raise AppError(
                    MISSING_REQUIRED_FIELD,
                    f"Required field '{field.label}' is missing",
                    status_code=400,
                )
            continue

        normalized = _validate_value_type(field, raw)
        to_persist.append((field, normalized))

    return to_persist


async def persist_field_responses(
    db: AsyncSession,
    pairs: list[tuple[EventRegistrationField, Any]],
    *,
    registration_id: uuid.UUID | None = None,
    team_member_id: uuid.UUID | None = None,
) -> None:
    if registration_id is None and team_member_id is None:
        raise ValueError("Either registration_id or team_member_id is required")
    for field, value in pairs:
        db.add(
            RegistrationFieldResponse(
                field_id=field.id,
                registration_id=registration_id,
                team_member_id=team_member_id,
                value=value,
            )
        )
