"""Admin operational detail and metrics endpoints."""
import uuid

import pytest
from sqlalchemy import select

from app.models.enums import RegistrationStatus
from app.models.profile import Profile
from app.models.registration import Registration
from app.services.admin_ops_service import (
    event_operations_metrics,
    get_admin_registration_detail,
    get_admin_team_detail,
)
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_registration_detail_includes_field_responses(db):
    event = await _make_event(db, name="Detail Reg Event")
    profile = await _make_user_profile(db, email=f"detail-{uuid.uuid4().hex}@test.edu")
    reg = Registration(event_id=event.id, profile_id=profile.id, status=RegistrationStatus.PENDING)
    db.add(reg)
    await db.flush()
    detail = await get_admin_registration_detail(db, reg.id)
    assert detail["event_id"] == event.id
    assert detail["participant"]["full_name"] == profile.full_name
    assert "field_responses" in detail


@pytest.mark.asyncio
async def test_event_operations_metrics(db):
    event = await _make_event(db, name="Metrics Event")
    metrics = await event_operations_metrics(db, event.id)
    assert metrics["event_id"] == event.id
    assert "registrations" in metrics
    assert "teams" in metrics


@pytest.mark.asyncio
async def test_team_detail_not_found(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_admin_team_detail(db, uuid.uuid4())
    assert exc.value.status_code == 404
