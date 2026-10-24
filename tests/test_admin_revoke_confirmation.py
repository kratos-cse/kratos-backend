"""Admin revoke confirmation: CONFIRMED → PENDING with QR/team cleanup."""
import uuid

import pytest
from fastapi import HTTPException

from app.main import app
from app.models.enums import RegistrationStatus, TeamStatus
from app.services import admin_ops_service as ops

from tests.conftest import (
    _make_event,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


def test_revoke_confirmation_route_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/admin/registrations/{registration_id}/revoke-confirmation" in paths


@pytest.mark.asyncio
async def test_revoke_solo_confirmed_to_pending(db):
    profile = await _make_user_profile(db, email=f"revoke-solo-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Revoke Solo {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    registration.status = RegistrationStatus.CONFIRMED
    await db.commit()

    result = await ops.revoke_registration_confirmation_admin(db, registration.id)
    assert result["status"] == RegistrationStatus.PENDING
    await db.refresh(registration)
    assert registration.status == RegistrationStatus.PENDING


@pytest.mark.asyncio
async def test_revoke_team_reverts_paid_to_forming(db):
    leader = await _make_user_profile(db, email=f"revoke-lead-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Revoke Team {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    team.status = TeamStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await db.commit()

    result = await ops.revoke_registration_confirmation_admin(db, registration.id)
    assert result["status"] == RegistrationStatus.PENDING
    assert result["team_status"] == TeamStatus.FORMING
    await db.refresh(team)
    assert team.status == TeamStatus.FORMING


@pytest.mark.asyncio
async def test_revoke_rejects_pending(db):
    profile = await _make_user_profile(db, email=f"revoke-pend-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Revoke Pend {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await ops.revoke_registration_confirmation_admin(db, registration.id)
    assert exc.value.status_code == 400
