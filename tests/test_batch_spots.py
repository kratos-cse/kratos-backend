"""Batch spots_remaining — avoids N+1 on GET /events."""
import uuid

import pytest

from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    RegistrationMode,
    RegistrationStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.registration import Registration
from app.services.event_service import batch_spots_remaining, invalidate_spots_cache

from .conftest import _make_user_profile, requires_db


@requires_db
@pytest.mark.asyncio
async def test_batch_spots_remaining_counts_solo_registrations(db):
    invalidate_spots_cache()
    event = Event(
        id=uuid.uuid4(),
        name="Cap Test",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=3,
        fee=0,
    )
    rules = EventRegistrationRule(
        event_id=event.id,
        registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
        allow_individual=True,
        team_min_size=1,
        team_max_size=1,
    )
    db.add(event)
    db.add(rules)
    await db.flush()

    for i in range(2):
        profile = await _make_user_profile(db, email=f"cap-{i}-{uuid.uuid4().hex}@test.local")
        db.add(
            Registration(
                event_id=event.id,
                profile_id=profile.id,
                status=RegistrationStatus.PENDING,
            )
        )
    await db.flush()

    remaining = await batch_spots_remaining(db, [(event, rules)])
    assert remaining[event.id] == 1
