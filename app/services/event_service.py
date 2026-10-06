"""
Computes the derived fields the API reference asks for on events:
"registration availability/status" (section 3) and remaining capacity —
neither of which is a stored column, both are derived from EVENTS +
EVENT_REGISTRATION_RULES + how many registrations/teams already exist.
"""
import time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uuid import UUID

from app.core.errors import CAPACITY_FULL, AppError
from app.models.enums import (
    CapacityType,
    EventRegistrationStatus,
    EventVisibility,
    RegistrationAvailability,
    RegistrationStatus,
    TeamMemberStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.registration import Registration
from app.models.team import TeamMember

# Short TTL for public GET /events (no user-specific state).
EVENTS_LIST_CACHE_TTL_SEC = 20.0
_events_list_cache: dict[str, object] = {"expires_at": 0.0, "payload": None}


def invalidate_events_list_cache() -> None:
    _events_list_cache["expires_at"] = 0.0
    _events_list_cache["payload"] = None


def get_cached_events_list():
    now = time.monotonic()
    if _events_list_cache["payload"] is not None and now < float(_events_list_cache["expires_at"] or 0):
        return _events_list_cache["payload"]
    return None


def set_cached_events_list(payload) -> None:
    _events_list_cache["payload"] = payload
    _events_list_cache["expires_at"] = time.monotonic() + EVENTS_LIST_CACHE_TTL_SEC


def resolve_registration_availability(
    event: Event,
    rules: Optional[EventRegistrationRule],
    spots_remaining_count: Optional[int],
) -> RegistrationAvailability:
    """
    Single source of truth for whether users can register right now.
    Precedence: visibility → admin registration_status → capacity.
    """
    if event.visibility != EventVisibility.PUBLISHED:
        return RegistrationAvailability.CLOSED
    if event.registration_status == EventRegistrationStatus.COMING_SOON:
        return RegistrationAvailability.COMING_SOON
    if event.registration_status != EventRegistrationStatus.OPEN:
        return RegistrationAvailability.CLOSED

    if spots_remaining_count is not None and spots_remaining_count <= 0:
        return RegistrationAvailability.FULL

    return RegistrationAvailability.OPEN


def is_registration_open(
    event: Event,
    rules: Optional[EventRegistrationRule],
    spots_remaining_count: Optional[int] = None,
) -> bool:
    """True only when resolve_registration_availability == OPEN."""
    return (
        resolve_registration_availability(event, rules, spots_remaining_count)
        == RegistrationAvailability.OPEN
    )


async def registration_availability_for_event(
    db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]
) -> tuple[RegistrationAvailability, Optional[int]]:
    remaining = await spots_remaining(db, event, rules)
    availability = resolve_registration_availability(event, rules, remaining)
    return availability, remaining


async def _count_used_capacity(db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]) -> int:
    """Capacity consumed only by CONFIRMED registrations (and roster members on confirmed teams)."""
    if rules and rules.capacity_type == CapacityType.TEAMS:
        result = await db.execute(
            select(func.count()).select_from(Registration).where(
                Registration.event_id == event.id,
                Registration.team_id.isnot(None),
                Registration.status == RegistrationStatus.CONFIRMED,
            )
        )
        return result.scalar_one()

    # PARTICIPANTS: confirmed solo registrations + ACTIVE members on confirmed team registrations.
    solo_count = (
        await db.execute(
            select(func.count()).select_from(Registration).where(
                Registration.event_id == event.id,
                Registration.profile_id.isnot(None),
                Registration.status == RegistrationStatus.CONFIRMED,
            )
        )
    ).scalar_one()

    member_count = (
        await db.execute(
            select(func.count())
            .select_from(TeamMember)
            .join(Registration, Registration.team_id == TeamMember.team_id)
            .where(
                TeamMember.event_id == event.id,
                Registration.event_id == event.id,
                Registration.status == RegistrationStatus.CONFIRMED,
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
        )
    ).scalar_one()

    return solo_count + member_count


async def _confirmation_capacity_units(
    db: AsyncSession,
    registration: Registration,
    rules: Optional[EventRegistrationRule],
) -> int:
    if registration.status == RegistrationStatus.CONFIRMED:
        return 0
    if rules and rules.capacity_type == CapacityType.TEAMS:
        return 1
    if registration.profile_id is not None:
        return 1
    if registration.team_id is not None:
        # Team leader payment confirms the registration and activates the leader only.
        return 1
    return 1


async def enforce_capacity_before_confirmation(db: AsyncSession, registration: Registration) -> None:
    """
    Serialize confirmations per event (row lock) and reject when confirmed capacity is full.
    No-op when registration is already CONFIRMED (idempotent payment retries).
    """
    if registration.status == RegistrationStatus.CONFIRMED:
        return

    event_result = await db.execute(
        select(Event).where(Event.id == registration.event_id).with_for_update()
    )
    event = event_result.scalar_one()
    rules_result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id)
    )
    rules = rules_result.scalar_one_or_none()
    if event.capacity is None:
        return

    units = await _confirmation_capacity_units(db, registration, rules)
    if units <= 0:
        return

    used = await _count_used_capacity(db, event, rules)
    if used + units > event.capacity:
        raise AppError(CAPACITY_FULL, "This event has reached capacity", status_code=409)


async def assert_event_has_participant_capacity(
    db: AsyncSession,
    event_id: UUID,
    *,
    additional_units: int = 1,
) -> None:
    """Lock event and ensure PARTICIPANTS-mode capacity can absorb additional active members."""
    if additional_units <= 0:
        return
    event_result = await db.execute(select(Event).where(Event.id == event_id).with_for_update())
    event = event_result.scalar_one()
    rules_result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id)
    )
    rules = rules_result.scalar_one_or_none()
    if event.capacity is None:
        return
    if rules and rules.capacity_type == CapacityType.TEAMS:
        return
    used = await _count_used_capacity(db, event, rules)
    if used + additional_units > event.capacity:
        raise AppError(CAPACITY_FULL, "This event has reached capacity", status_code=409)


_SPOTS_CACHE_TTL_SEC = 5.0
_spots_cache: dict[object, tuple[float, int]] = {}


def invalidate_spots_cache(event_id: object | None = None) -> None:
    if event_id is not None:
        _spots_cache.pop(event_id, None)
    else:
        _spots_cache.clear()


async def batch_spots_remaining(
    db: AsyncSession,
    rows: list[tuple[Event, Optional[EventRegistrationRule]]],
) -> dict[object, Optional[int]]:
    """
    Batch capacity lookup for list endpoints — avoids N+1 per-event count queries.
    Returns event_id -> remaining (None when event has no capacity cap).
    """
    result: dict[object, Optional[int]] = {}
    uncached: list[tuple[Event, Optional[EventRegistrationRule]]] = []
    now = time.monotonic()

    for event, rules in rows:
        if event.capacity is None:
            result[event.id] = None
            continue
        cached = _spots_cache.get(event.id)
        if cached is not None and now < cached[0]:
            result[event.id] = cached[1]
        else:
            uncached.append((event, rules))

    if not uncached:
        return result

    team_ids = [event.id for event, rules in uncached if rules and rules.capacity_type == CapacityType.TEAMS]
    participant_ids = [
        event.id for event, rules in uncached if not rules or rules.capacity_type != CapacityType.TEAMS
    ]

    used: dict[object, int] = {}

    if team_ids:
        team_rows = await db.execute(
            select(Registration.event_id, func.count())
            .where(
                Registration.event_id.in_(team_ids),
                Registration.team_id.isnot(None),
                Registration.status == RegistrationStatus.CONFIRMED,
            )
            .group_by(Registration.event_id)
        )
        for event_id, count in team_rows.all():
            used[event_id] = int(count)

    if participant_ids:
        solo_rows = await db.execute(
            select(Registration.event_id, func.count())
            .where(
                Registration.event_id.in_(participant_ids),
                Registration.profile_id.isnot(None),
                Registration.status == RegistrationStatus.CONFIRMED,
            )
            .group_by(Registration.event_id)
        )
        solo_by_event = {event_id: int(count) for event_id, count in solo_rows.all()}

        member_rows = await db.execute(
            select(TeamMember.event_id, func.count())
            .join(Registration, Registration.team_id == TeamMember.team_id)
            .where(
                TeamMember.event_id.in_(participant_ids),
                Registration.event_id.in_(participant_ids),
                Registration.status == RegistrationStatus.CONFIRMED,
                TeamMember.status == TeamMemberStatus.ACTIVE,
            )
            .group_by(TeamMember.event_id)
        )
        member_by_event = {event_id: int(count) for event_id, count in member_rows.all()}

        for event_id in participant_ids:
            used[event_id] = solo_by_event.get(event_id, 0) + member_by_event.get(event_id, 0)

    for event, _rules in uncached:
        consumed = used.get(event.id, 0)
        remaining = max(int(event.capacity) - consumed, 0)
        result[event.id] = remaining
        _spots_cache[event.id] = (now + _SPOTS_CACHE_TTL_SEC, remaining)

    return result


async def spots_remaining(
    db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]
) -> Optional[int]:
    if event.capacity is None:
        return None

    now = time.monotonic()
    cached = _spots_cache.get(event.id)
    if cached is not None and now < cached[0]:
        return cached[1]

    used = await _count_used_capacity(db, event, rules)
    remaining = max(event.capacity - used, 0)
    _spots_cache[event.id] = (now + _SPOTS_CACHE_TTL_SEC, remaining)
    return remaining
