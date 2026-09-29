"""Capacity enforcement unchanged after custom fields work."""
import pytest
from fastapi import HTTPException

from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services.registration_service import create_registration
from tests.conftest import _make_event, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_full_event_still_rejects_registration(db):
    event = await _make_event(db, name="Full Event")
    event.capacity = 0
    await db.flush()
    profile = await _make_user_profile(db, email="full@test.edu")
    with pytest.raises(HTTPException) as exc:
        await create_registration(
            db,
            event.id,
            profile,
            RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
        )
    assert exc.value.status_code == 400
    assert "capacity" in str(exc.value.detail).lower()
