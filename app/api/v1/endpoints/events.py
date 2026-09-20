import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.event import Event
from app.schemas.event import EventDetail, EventListItem
from app.services.event_service import is_registration_open, spots_remaining

router = APIRouter(prefix="/events", tags=["Events"])


@router.get("", response_model=list[EventListItem])
async def list_events(db: AsyncSession = Depends(get_db)):
    """Public event catalogue with computed registration availability."""
    result = await db.execute(select(Event).options(selectinload(Event.rules)).order_by(Event.starts_at))
    events = result.scalars().all()

    items = []
    for event in events:
        rules = event.rules
        items.append(
            EventListItem(
                id=event.id,
                name=event.name,
                short_desc=event.short_desc,
                category=event.category,
                fee=event.fee,
                venue=event.venue,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                status=event.status,
                registration_open=is_registration_open(event, rules),
                allow_individual=rules.allow_individual if rules else True,
                team_min_size=rules.team_min_size if rules else 1,
                team_max_size=rules.team_max_size if rules else 1,
            )
        )
    return items


@router.get("/{event_id}", response_model=EventDetail)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Complete event configuration needed by the registration frontend."""
    result = await db.execute(select(Event).options(selectinload(Event.rules)).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    rules = event.rules
    remaining = await spots_remaining(db, event, rules)

    return EventDetail(
        id=event.id,
        name=event.name,
        short_desc=event.short_desc,
        long_desc=event.long_desc,
        category=event.category,
        coordinator=event.coordinator,
        coord_contact=event.coord_contact,
        fee=event.fee,
        venue=event.venue,
        capacity=event.capacity,
        whatsapp_group_link=event.whatsapp_group_link,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        status=event.status,
        registration_open=is_registration_open(event, rules),
        spots_remaining=remaining,
        team_min_size=rules.team_min_size if rules else 1,
        team_max_size=rules.team_max_size if rules else 1,
        allow_individual=rules.allow_individual if rules else True,
        fee_charge_model=rules.fee_charge_model if rules else None,
        capacity_type=rules.capacity_type if rules else None,
        member_registration_mode=rules.member_registration_mode if rules else None,
        allow_team_invite_flow=rules.allow_team_invite_flow if rules else False,
        requires_qr_checkin=rules.requires_qr_checkin if rules else True,
        custom_fields=rules.custom_fields if rules else None,
        registration_opens_at=rules.registration_opens_at if rules else None,
        registration_closes_at=rules.registration_closes_at if rules else None,
    )
