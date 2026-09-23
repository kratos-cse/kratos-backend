import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import (
    EVENT_NOT_FOUND,
    FORBIDDEN,
    WHATSAPP_UNAVAILABLE,
    AppError,
)
from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.enums import RegistrationStatus, TeamMemberStatus, TeamStatus
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.event import EventDetail, EventListItem, EventWhatsAppOut
from app.services.event_service import (
    get_cached_events_list,
    is_registration_open,
    set_cached_events_list,
    spots_remaining,
)
from app.services.sample_events import get_sample_event_detail, get_sample_event_list

router = APIRouter(prefix="/events", tags=["Events"])


@router.get("", response_model=list[EventListItem])
async def list_events(db: AsyncSession = Depends(get_db)):
    """Public event catalogue — lightweight projection, no per-event capacity."""
    cached = get_cached_events_list()
    if cached is not None:
        return cached

    rows = []
    try:
        result = await db.execute(
            select(Event, EventRegistrationRule)
            .outerjoin(EventRegistrationRule, EventRegistrationRule.event_id == Event.id)
            .order_by(Event.starts_at.nulls_last())
        )
        rows = result.all()
    except Exception:
        rows = []

    if not rows:
        sample_items = get_sample_event_list()
        set_cached_events_list(sample_items)
        return sample_items

    items: list[EventListItem] = []
    for event, rules in rows:
        items.append(
            EventListItem(
                id=event.id,
                name=event.name,
                tagline=event.tagline,
                short_desc=event.short_desc,
                category=event.category,
                fee=event.fee,
                venue=event.venue,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                slot=event.slot,
                status=event.status,
                registration_open=is_registration_open(event, rules),
                allow_individual=rules.allow_individual if rules else True,
                team_min_size=rules.team_min_size if rules else 1,
                team_max_size=rules.team_max_size if rules else 1,
                required_member_count=getattr(rules, "required_member_count", None) or (rules.team_min_size if rules else 1),
                substitute_count=getattr(rules, "substitute_count", None)
                if rules and getattr(rules, "substitute_count", None) is not None
                else max(0, (rules.team_max_size if rules else 1) - (rules.team_min_size if rules else 1)),
            )
        )

    set_cached_events_list(items)
    return items


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Complete event configuration needed by the registration frontend."""
    sample_detail = get_sample_event_detail(event_id)
    if sample_detail is not None:
        return sample_detail

    event = None
    try:
        result = await db.execute(select(Event).options(selectinload(Event.rules)).where(Event.id == event_id))
        event = result.scalar_one_or_none()
    except Exception:
        event = None

    if event is None:
        raise AppError(EVENT_NOT_FOUND, "Event not found", status_code=status.HTTP_404_NOT_FOUND)

    rules = event.rules
    remaining = await spots_remaining(db, event, rules)

    return EventDetail(
        id=event.id,
        name=event.name,
        tagline=event.tagline,
        short_desc=event.short_desc,
        long_desc=event.long_desc,
        category=event.category,
        coordinator=event.coordinator,
        coord_contact=event.coord_contact,
        fee=event.fee,
        venue=event.venue,
        capacity=event.capacity,
        whatsapp_group_available=bool(event.whatsapp_group_link),
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        slot=event.slot,
        status=event.status,
        registration_open=is_registration_open(event, rules),
        spots_remaining=remaining,
        team_min_size=rules.team_min_size if rules else 1,
        team_max_size=rules.team_max_size if rules else 1,
        required_member_count=getattr(rules, "required_member_count", None) or (rules.team_min_size if rules else 1),
        substitute_count=(
            getattr(rules, "substitute_count", None)
            if rules and getattr(rules, "substitute_count", None) is not None
            else max(0, (rules.team_max_size if rules else 1) - (rules.team_min_size if rules else 1))
        ),
        allow_individual=rules.allow_individual if rules else True,
        registration_mode=rules.registration_mode if rules else None,
        capacity_type=rules.capacity_type if rules else None,
        member_registration_mode=rules.member_registration_mode if rules else None,
        allow_team_invite_flow=rules.allow_team_invite_flow if rules else False,
        requires_qr_checkin=rules.requires_qr_checkin if rules else True,
        custom_fields=rules.custom_fields if rules else None,
        registration_opens_at=rules.registration_opens_at if rules else None,
        registration_closes_at=rules.registration_closes_at if rules else None,
    )


async def _profile_entitled_to_whatsapp(db: AsyncSession, event_id: uuid.UUID, profile: Profile) -> bool:
    """Entitled if confirmed solo registration OR active member of a paid/complete team."""
    solo = await db.execute(
        select(Registration.id).where(
            Registration.event_id == event_id,
            Registration.profile_id == profile.id,
            Registration.status == RegistrationStatus.CONFIRMED,
        )
    )
    if solo.scalar_one_or_none() is not None:
        return True

    member = await db.execute(
        select(TeamMember.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.event_id == event_id,
            TeamMember.profile_id == profile.id,
            TeamMember.status.in_([TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT]),
            Team.status.in_([TeamStatus.PAID, TeamStatus.COMPLETE]),
        )
    )
    return member.scalar_one_or_none() is not None


@router.get("/{event_id}/whatsapp", response_model=EventWhatsAppOut)
async def get_event_whatsapp(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    """Return WhatsApp group URL only to entitled authenticated participants."""
    try:
        result = await db.execute(select(Event).where(Event.id == event_id))
        event = result.scalar_one_or_none()
        if event is None:
            raise AppError(EVENT_NOT_FOUND, "Event not found", status_code=status.HTTP_404_NOT_FOUND)
        if not event.whatsapp_group_link:
            raise AppError(WHATSAPP_UNAVAILABLE, "No WhatsApp group configured for this event", status_code=404)
        if not await _profile_entitled_to_whatsapp(db, event_id, profile):
            raise AppError(
                FORBIDDEN,
                "WhatsApp group is available after confirmed registration for this event",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return EventWhatsAppOut(event_id=event.id, whatsapp_group_link=event.whatsapp_group_link)
    except AppError:
        raise
    except Exception:
        from app.services.sample_events import get_sample_event_detail

        sample = get_sample_event_detail(event_id)
        if sample:
            return EventWhatsAppOut(
                event_id=event_id,
                whatsapp_group_link=sample.whatsapp_group_link or "https://chat.whatsapp.com/sample-kratos-group",
            )
        raise AppError(EVENT_NOT_FOUND, "Event not found", status_code=status.HTTP_404_NOT_FOUND)
