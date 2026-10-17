"""Project Expo category uses the same registration stack as other catalogue events."""
import uuid

import pytest

from app.models.enums import EventCategory, EventRegistrationStatus, EventVisibility, RegistrationMode
from app.models.event import Event, EventRegistrationRule
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services.admin_ops_service import event_operations_metrics
from app.services.registration_service import create_registration

from .conftest import _make_user_profile, refresh_registration, requires_db


def test_project_expo_enum_value():
    assert EventCategory.PROJECT_EXPO.value == "PROJECT_EXPO"


@requires_db
@pytest.mark.asyncio
async def test_project_expo_registration_not_counted_under_other_category(db):
    technical = Event(
        id=uuid.uuid4(),
        name="Tech cap",
        category=EventCategory.TECHNICAL,
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=10,
        fee=0,
    )
    expo = Event(
        id=uuid.uuid4(),
        name="Expo showcase",
        category=EventCategory.PROJECT_EXPO,
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=2,
        fee=0,
    )
    for event in (technical, expo):
        db.add(event)
        db.add(
            EventRegistrationRule(
                event_id=event.id,
                registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
                allow_individual=True,
                team_min_size=1,
                team_max_size=1,
            )
        )
    await db.flush()

    profile = await _make_user_profile(db, email=f"expo-{uuid.uuid4().hex}@t.local")
    reg = await create_registration(
        db,
        expo.id,
        profile,
        RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
    )
    await db.commit()
    reg = await refresh_registration(db, reg.id)

    assert reg.event_id == expo.id
    expo_metrics = await event_operations_metrics(db, expo.id)
    tech_metrics = await event_operations_metrics(db, technical.id)
    assert expo_metrics["registrations_total"] >= 1
    assert tech_metrics["registrations_total"] == 0
