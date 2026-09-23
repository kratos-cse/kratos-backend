"""Integration tests for event visibility + registration controls."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.enums import EventRegistrationStatus, EventVisibility, RegistrationAvailability
from app.models.event import Event
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services.event_projection import build_event_state
from app.services.event_service import resolve_registration_availability
from app.services.event_state import open_registration, publish_event, unpublish_event
from app.services.registration_service import create_registration
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_publish_does_not_auto_open_registration(db):
    event = await _make_event(db, name="Publish Flow")
    event.visibility = EventVisibility.UNPUBLISHED
    event.registration_status = EventRegistrationStatus.CLOSED
    await db.flush()

    publish_event(event)
    await db.flush()

    assert event.visibility == EventVisibility.PUBLISHED
    assert event.registration_status == EventRegistrationStatus.CLOSED


@pytest.mark.asyncio
async def test_unpublish_closes_registration(db):
    event = await _make_event(db, name="Unpublish Flow")
    event.visibility = EventVisibility.PUBLISHED
    event.registration_status = EventRegistrationStatus.OPEN
    await db.flush()

    unpublish_event(event)
    await db.flush()

    assert event.visibility == EventVisibility.UNPUBLISHED
    assert event.registration_status == EventRegistrationStatus.CLOSED


@pytest.mark.asyncio
async def test_cannot_open_registration_when_unpublished(db):
    event = await _make_event(db, name="Blocked Open")
    event.visibility = EventVisibility.UNPUBLISHED
    event.registration_status = EventRegistrationStatus.CLOSED
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        open_registration(event)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_published_closed_availability(db):
    event = await _make_event(db, name="Closed Reg")
    event.visibility = EventVisibility.PUBLISHED
    event.registration_status = EventRegistrationStatus.CLOSED
    rules = event.rules
    await db.flush()

    state = await build_event_state(db, event, rules)
    assert state["registration_availability"] == RegistrationAvailability.CLOSED
    assert state["registration_open"] is False


@pytest.mark.asyncio
async def test_registration_rejects_unpublished_event(db):
    event = await _make_event(db, name="Hidden")
    event.visibility = EventVisibility.UNPUBLISHED
    event.registration_status = EventRegistrationStatus.OPEN
    await db.flush()
    profile = await _make_user_profile(db, email="hidden@test.local")

    with pytest.raises(HTTPException) as exc:
        await create_registration(
            db,
            event.id,
            profile,
            RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
        )
    assert exc.value.status_code == 400
    assert "not available" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_registration_rejects_closed_registration(db):
    event = await _make_event(db, name="Closed")
    event.registration_status = EventRegistrationStatus.CLOSED
    await db.flush()
    profile = await _make_user_profile(db, email="closed@test.local")

    with pytest.raises(HTTPException) as exc:
        await create_registration(
            db,
            event.id,
            profile,
            RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_full_capacity_availability(db):
    event = await _make_event(db, name="Full Event")
    event.capacity = 0
    rules = event.rules
    await db.flush()

    availability = resolve_registration_availability(event, rules, 0)
    assert availability == RegistrationAvailability.FULL


@pytest.mark.asyncio
async def test_public_filter_query_only_published(db):
    published = await _make_event(db, name="Visible")
    published.visibility = EventVisibility.PUBLISHED
    hidden = await _make_event(db, name="Invisible")
    hidden.visibility = EventVisibility.UNPUBLISHED
    await db.flush()

    result = await db.execute(
        select(Event).where(Event.visibility == EventVisibility.PUBLISHED)
    )
    names = {e.name for e in result.scalars().all()}
    assert "Visible" in names
    assert "Invisible" not in names
