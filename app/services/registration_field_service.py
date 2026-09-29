"""Admin CRUD for event registration form fields."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RegistrationFieldScope
from app.models.event import Event
from app.models.event_content import EventRegistrationField
from app.schemas.event_content import RegistrationFieldCreate, RegistrationFieldOut, RegistrationFieldUpdate, RegistrationFormOut


async def _get_event_or_404(db: AsyncSession, event_id: uuid.UUID) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


async def list_registration_fields(db: AsyncSession, event_id: uuid.UUID) -> list[EventRegistrationField]:
    await _get_event_or_404(db, event_id)
    result = await db.execute(
        select(EventRegistrationField)
        .where(EventRegistrationField.event_id == event_id)
        .order_by(EventRegistrationField.scope, EventRegistrationField.display_order)
    )
    return list(result.scalars().all())


async def create_registration_field(
    db: AsyncSession, event_id: uuid.UUID, payload: RegistrationFieldCreate
) -> EventRegistrationField:
    await _get_event_or_404(db, event_id)
    field = EventRegistrationField(event_id=event_id, **payload.model_dump())
    db.add(field)
    await db.commit()
    await db.refresh(field)
    return field


async def update_registration_field(
    db: AsyncSession,
    event_id: uuid.UUID,
    field_id: uuid.UUID,
    payload: RegistrationFieldUpdate,
) -> EventRegistrationField:
    result = await db.execute(
        select(EventRegistrationField).where(
            EventRegistrationField.id == field_id,
            EventRegistrationField.event_id == event_id,
        )
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration field not found")
    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(field, key, val)
    await db.commit()
    await db.refresh(field)
    return field


async def delete_registration_field(db: AsyncSession, event_id: uuid.UUID, field_id: uuid.UUID) -> None:
    result = await db.execute(
        select(EventRegistrationField).where(
            EventRegistrationField.id == field_id,
            EventRegistrationField.event_id == event_id,
        )
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration field not found")
    await db.delete(field)
    await db.commit()


async def reorder_registration_fields(
    db: AsyncSession, event_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> list[EventRegistrationField]:
    fields = await list_registration_fields(db, event_id)
    by_id = {f.id: f for f in fields}
    for idx, fid in enumerate(ordered_ids):
        if fid in by_id:
            by_id[fid].display_order = idx
    await db.commit()
    return await list_registration_fields(db, event_id)


async def get_registration_form(db: AsyncSession, event_id: uuid.UUID) -> RegistrationFormOut:
    result = await db.execute(
        select(EventRegistrationField)
        .where(
            EventRegistrationField.event_id == event_id,
            EventRegistrationField.is_visible.is_(True),
        )
        .order_by(EventRegistrationField.display_order)
    )
    fields = list(result.scalars().all())
    reg_fields = [RegistrationFieldOut.model_validate(f) for f in fields if f.scope == RegistrationFieldScope.REGISTRATION]
    member_fields = [
        RegistrationFieldOut.model_validate(f) for f in fields if f.scope == RegistrationFieldScope.TEAM_MEMBER
    ]
    return RegistrationFormOut(registration_fields=reg_fields, team_member_fields=member_fields)
