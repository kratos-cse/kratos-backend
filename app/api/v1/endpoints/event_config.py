"""Admin CRUD for event content, coordinators, and registration fields."""
import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import require_scoped_event_access
from app.core.permissions import EVENT_EDIT, EVENT_READ
from app.db.session import get_db
from app.models.admin import AdminUser
from app.schemas.event_content import (
    ContentSectionCreate,
    ContentSectionOut,
    ContentSectionUpdate,
    CoordinatorCreate,
    CoordinatorOut,
    CoordinatorUpdate,
    RegistrationFieldCreate,
    RegistrationFieldOut,
    RegistrationFieldUpdate,
    ReorderBody,
)
from app.services import event_content_service as content_svc
from app.services import registration_field_service as field_svc
from app.services.event_service import invalidate_events_list_cache

router = APIRouter(prefix="/admin/events", tags=["Event Configuration"])


def _success(data):
    return {"status": "success", "data": data}


# --- Content sections ---


@router.get("/{event_id}/content-sections")
async def list_content_sections(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_READ)),
):
    sections = await content_svc.list_content_sections(db, event_id)
    return _success([ContentSectionOut.model_validate(s) for s in sections])


@router.post("/{event_id}/content-sections", status_code=status.HTTP_201_CREATED)
async def create_content_section(
    event_id: uuid.UUID,
    body: ContentSectionCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    section = await content_svc.create_content_section(db, event_id, body)
    invalidate_events_list_cache()
    return _success(ContentSectionOut.model_validate(section))


@router.patch("/{event_id}/content-sections/{section_id}")
async def update_content_section(
    event_id: uuid.UUID,
    section_id: uuid.UUID,
    body: ContentSectionUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    section = await content_svc.update_content_section(db, event_id, section_id, body)
    invalidate_events_list_cache()
    return _success(ContentSectionOut.model_validate(section))


@router.delete("/{event_id}/content-sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_content_section(
    event_id: uuid.UUID,
    section_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    await content_svc.delete_content_section(db, event_id, section_id)
    invalidate_events_list_cache()


@router.post("/{event_id}/content-sections/reorder")
async def reorder_content_sections(
    event_id: uuid.UUID,
    body: ReorderBody,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    sections = await content_svc.reorder_content_sections(db, event_id, body.ordered_ids)
    invalidate_events_list_cache()
    return _success([ContentSectionOut.model_validate(s) for s in sections])


# --- Coordinators (public contact info) ---


@router.get("/{event_id}/coordinators")
async def list_coordinators(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_READ)),
):
    coords = await content_svc.list_coordinators(db, event_id)
    return _success([CoordinatorOut.model_validate(c) for c in coords])


@router.post("/{event_id}/coordinators", status_code=status.HTTP_201_CREATED)
async def create_coordinator(
    event_id: uuid.UUID,
    body: CoordinatorCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    coord = await content_svc.create_coordinator(db, event_id, body)
    invalidate_events_list_cache()
    return _success(CoordinatorOut.model_validate(coord))


@router.patch("/{event_id}/coordinators/{coordinator_id}")
async def update_coordinator(
    event_id: uuid.UUID,
    coordinator_id: uuid.UUID,
    body: CoordinatorUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    coord = await content_svc.update_coordinator(db, event_id, coordinator_id, body)
    invalidate_events_list_cache()
    return _success(CoordinatorOut.model_validate(coord))


@router.delete("/{event_id}/coordinators/{coordinator_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_coordinator(
    event_id: uuid.UUID,
    coordinator_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    await content_svc.delete_coordinator(db, event_id, coordinator_id)
    invalidate_events_list_cache()


@router.post("/{event_id}/coordinators/reorder")
async def reorder_coordinators(
    event_id: uuid.UUID,
    body: ReorderBody,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    coords = await content_svc.reorder_coordinators(db, event_id, body.ordered_ids)
    invalidate_events_list_cache()
    return _success([CoordinatorOut.model_validate(c) for c in coords])


# --- Registration fields ---


@router.get("/{event_id}/registration-fields")
async def list_registration_fields(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_READ)),
):
    fields = await field_svc.list_registration_fields(db, event_id)
    return _success([RegistrationFieldOut.model_validate(f) for f in fields])


@router.post("/{event_id}/registration-fields", status_code=status.HTTP_201_CREATED)
async def create_registration_field(
    event_id: uuid.UUID,
    body: RegistrationFieldCreate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    field = await field_svc.create_registration_field(db, event_id, body)
    return _success(RegistrationFieldOut.model_validate(field))


@router.patch("/{event_id}/registration-fields/{field_id}")
async def update_registration_field(
    event_id: uuid.UUID,
    field_id: uuid.UUID,
    body: RegistrationFieldUpdate,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    field = await field_svc.update_registration_field(db, event_id, field_id, body)
    return _success(RegistrationFieldOut.model_validate(field))


@router.delete("/{event_id}/registration-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registration_field(
    event_id: uuid.UUID,
    field_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    await field_svc.delete_registration_field(db, event_id, field_id)


@router.post("/{event_id}/registration-fields/reorder")
async def reorder_registration_fields(
    event_id: uuid.UUID,
    body: ReorderBody,
    db: AsyncSession = Depends(get_db),
    _: AdminUser = Depends(require_scoped_event_access(EVENT_EDIT, write=True)),
):
    fields = await field_svc.reorder_registration_fields(db, event_id, body.ordered_ids)
    return _success([RegistrationFieldOut.model_validate(f) for f in fields])
