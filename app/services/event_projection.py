"""Shared event API projection — one canonical shape for public + admin clients."""
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event, EventRegistrationRule
from app.services.event_service import (
    is_registration_open,
    registration_availability_for_event,
)


def _rule_int(rules: Optional[EventRegistrationRule], attr: str, default: int) -> int:
    if not rules:
        return default
    return int(getattr(rules, attr, default) or default)


def _substitute_count(rules: Optional[EventRegistrationRule]) -> int:
    if not rules:
        return 0
    explicit = getattr(rules, "substitute_count", None)
    if explicit is not None:
        return int(explicit)
    return max(0, int(rules.team_max_size) - int(rules.team_min_size))


def build_event_state_from_remaining(
    event: Event,
    rules: Optional[EventRegistrationRule],
    spots_remaining_count: Optional[int],
) -> dict[str, Any]:
    """Sync projection when spots remaining is already known (e.g. batched list)."""
    from app.services.event_service import resolve_registration_availability  # local: avoid cycle at import

    availability = resolve_registration_availability(event, rules, spots_remaining_count)
    return {
        "registration_availability": availability,
        "registration_open": is_registration_open(event, rules, spots_remaining_count),
        "spots_remaining": spots_remaining_count,
        "allow_individual": rules.allow_individual if rules else True,
        "registration_mode": rules.registration_mode if rules else None,
        "team_min_size": _rule_int(rules, "team_min_size", 1),
        "team_max_size": _rule_int(rules, "team_max_size", 1),
        "required_member_count": _rule_int(rules, "required_member_count", _rule_int(rules, "team_min_size", 1)),
        "substitute_count": _substitute_count(rules),
    }


async def build_event_state(
    db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]
) -> dict[str, Any]:
    """Derived registration fields used by public and admin responses."""
    availability, remaining = await registration_availability_for_event(db, event, rules)
    return build_event_state_from_remaining(event, rules, remaining)
