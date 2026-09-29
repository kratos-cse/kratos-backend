"""Admin CRUD for event content sections and coordinators."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.event_content import EventContentSection, EventCoordinator
from app.schemas.event_content import (
    ContentSectionCreate,
    ContentSectionUpdate,
    CoordinatorCreate,
    CoordinatorUpdate,
)


async def _get_event_or_404(db: AsyncSession, event_id: uuid.UUID) -> Event:
    result = await db.execute(select(Event).where(Event.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return event


async def list_content_sections(db: AsyncSession, event_id: uuid.UUID) -> list[EventContentSection]:
    await _get_event_or_404(db, event_id)
    result = await db.execute(
        select(EventContentSection)
        .where(EventContentSection.event_id == event_id)
        .order_by(EventContentSection.display_order)
    )
    return list(result.scalars().all())


async def create_content_section(
    db: AsyncSession, event_id: uuid.UUID, payload: ContentSectionCreate
) -> EventContentSection:
    await _get_event_or_404(db, event_id)
    section = EventContentSection(event_id=event_id, **payload.model_dump())
    db.add(section)
    await db.commit()
    await db.refresh(section)
    return section


async def update_content_section(
    db: AsyncSession,
    event_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: ContentSectionUpdate,
) -> EventContentSection:
    result = await db.execute(
        select(EventContentSection).where(
            EventContentSection.id == section_id,
            EventContentSection.event_id == event_id,
        )
    )
    section = result.scalar_one_or_none()
    if section is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content section not found")
    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(section, key, val)
    await db.commit()
    await db.refresh(section)
    return section


async def delete_content_section(db: AsyncSession, event_id: uuid.UUID, section_id: uuid.UUID) -> None:
    result = await db.execute(
        select(EventContentSection).where(
            EventContentSection.id == section_id,
            EventContentSection.event_id == event_id,
        )
    )
    section = result.scalar_one_or_none()
    if section is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content section not found")
    await db.delete(section)
    await db.commit()


async def reorder_content_sections(
    db: AsyncSession, event_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> list[EventContentSection]:
    sections = await list_content_sections(db, event_id)
    by_id = {s.id: s for s in sections}
    for idx, sid in enumerate(ordered_ids):
        if sid in by_id:
            by_id[sid].display_order = idx
    await db.commit()
    return await list_content_sections(db, event_id)


async def list_coordinators(db: AsyncSession, event_id: uuid.UUID) -> list[EventCoordinator]:
    await _get_event_or_404(db, event_id)
    result = await db.execute(
        select(EventCoordinator)
        .where(EventCoordinator.event_id == event_id)
        .order_by(EventCoordinator.display_order)
    )
    return list(result.scalars().all())


async def create_coordinator(
    db: AsyncSession, event_id: uuid.UUID, payload: CoordinatorCreate
) -> EventCoordinator:
    await _get_event_or_404(db, event_id)
    coord = EventCoordinator(event_id=event_id, **payload.model_dump())
    db.add(coord)
    await db.commit()
    await db.refresh(coord)
    return coord


async def update_coordinator(
    db: AsyncSession,
    event_id: uuid.UUID,
    coordinator_id: uuid.UUID,
    payload: CoordinatorUpdate,
) -> EventCoordinator:
    result = await db.execute(
        select(EventCoordinator).where(
            EventCoordinator.id == coordinator_id,
            EventCoordinator.event_id == event_id,
        )
    )
    coord = result.scalar_one_or_none()
    if coord is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coordinator not found")
    for key, val in payload.model_dump(exclude_unset=True).items():
        setattr(coord, key, val)
    await db.commit()
    await db.refresh(coord)
    return coord


async def delete_coordinator(db: AsyncSession, event_id: uuid.UUID, coordinator_id: uuid.UUID) -> None:
    result = await db.execute(
        select(EventCoordinator).where(
            EventCoordinator.id == coordinator_id,
            EventCoordinator.event_id == event_id,
        )
    )
    coord = result.scalar_one_or_none()
    if coord is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coordinator not found")
    await db.delete(coord)
    await db.commit()


async def reorder_coordinators(
    db: AsyncSession, event_id: uuid.UUID, ordered_ids: list[uuid.UUID]
) -> list[EventCoordinator]:
    coords = await list_coordinators(db, event_id)
    by_id = {c.id: c for c in coords}
    for idx, cid in enumerate(ordered_ids):
        if cid in by_id:
            by_id[cid].display_order = idx
    await db.commit()
    return await list_coordinators(db, event_id)


async def get_visible_content_sections(db: AsyncSession, event_id: uuid.UUID) -> list[EventContentSection]:
    result = await db.execute(
        select(EventContentSection)
        .where(
            EventContentSection.event_id == event_id,
            EventContentSection.is_visible.is_(True),
        )
        .order_by(EventContentSection.display_order)
    )
    return list(result.scalars().all())


async def get_visible_coordinators(db: AsyncSession, event_id: uuid.UUID) -> list[EventCoordinator]:
    result = await db.execute(
        select(EventCoordinator)
        .where(EventCoordinator.event_id == event_id)
        .order_by(EventCoordinator.display_order)
    )
    return list(result.scalars().all())
