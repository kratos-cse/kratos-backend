"""
Computes the derived fields the API reference asks for on events:
"registration availability/status" (section 3) and remaining capacity —
neither of which is a stored column, both are derived from EVENTS +
EVENT_REGISTRATION_RULES + how many registrations/teams already exist.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import CapacityType, EventStatus, RegistrationStatus, TeamMemberStatus, TeamStatus
from app.models.event import Event, EventRegistrationRule
from app.models.registration import Registration
from app.models.team import Team, TeamMember


def is_registration_open(event: Event, rules: Optional[EventRegistrationRule]) -> bool:
    """Effective openness = admin status OPEN AND within the registration window."""
    if event.status != EventStatus.OPEN:
        return False

    now = datetime.now(timezone.utc)
    if rules:
        if rules.registration_opens_at and now < rules.registration_opens_at:
            return False
        if rules.registration_closes_at and now > rules.registration_closes_at:
            return False
    return True


async def _count_used_capacity(db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]) -> int:
    if rules and rules.capacity_type == CapacityType.TEAMS:
        result = await db.execute(
            select(func.count()).select_from(Team).where(
                Team.event_id == event.id,
                Team.status != TeamStatus.CANCELLED,
            )
        )
        return result.scalar_one()

    # PARTICIPANTS (default): solo registrations + active/pending team members
    solo_count = (
        await db.execute(
            select(func.count()).select_from(Registration).where(
                Registration.event_id == event.id,
                Registration.profile_id.isnot(None),
                Registration.status != RegistrationStatus.CANCELLED,
            )
        )
    ).scalar_one()

    member_count = (
        await db.execute(
            select(func.count()).select_from(TeamMember).where(
                TeamMember.event_id == event.id,
                TeamMember.status.in_([TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT]),
            )
        )
    ).scalar_one()

    return solo_count + member_count


async def spots_remaining(
    db: AsyncSession, event: Event, rules: Optional[EventRegistrationRule]
) -> Optional[int]:
    if event.capacity is None:
        return None
    used = await _count_used_capacity(db, event, rules)
    return max(event.capacity - used, 0)
