"""Tests for event content sections and coordinators."""
import uuid

import pytest
from sqlalchemy import select

from app.models.event_content import EventContentSection, EventCoordinator
from app.models.enums import ContentSectionType, EventVisibility
from app.schemas.event_content import ContentSectionCreate, CoordinatorCreate
from app.services import event_content_service as svc
from tests.conftest import _make_event, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_create_and_list_content_sections(db):
    event = await _make_event(db, name="Content Test")
    section = await svc.create_content_section(
        db,
        event.id,
        ContentSectionCreate(title="Rules", content="No cheating", section_type=ContentSectionType.RULES),
    )
    assert section.title == "Rules"
    items = await svc.list_content_sections(db, event.id)
    assert len(items) == 1
    assert items[0].id == section.id


@pytest.mark.asyncio
async def test_create_multiple_coordinators(db):
    event = await _make_event(db, name="Coord Test")
    c1 = await svc.create_coordinator(
        db, event.id, CoordinatorCreate(name="Alice", contact="alice@test.edu", role="Faculty")
    )
    c2 = await svc.create_coordinator(
        db, event.id, CoordinatorCreate(name="Bob", contact="+919876543210", display_order=1)
    )
    coords = await svc.get_visible_coordinators(db, event.id)
    assert len(coords) == 2
    assert {c.id for c in coords} == {c1.id, c2.id}


@pytest.mark.asyncio
async def test_reorder_coordinators(db):
    event = await _make_event(db, name="Reorder Test")
    c1 = await svc.create_coordinator(db, event.id, CoordinatorCreate(name="First", contact="a@b.c"))
    c2 = await svc.create_coordinator(db, event.id, CoordinatorCreate(name="Second", contact="b@c.d"))
    reordered = await svc.reorder_coordinators(db, event.id, [c2.id, c1.id])
    assert reordered[0].id == c2.id
    assert reordered[0].display_order == 0
