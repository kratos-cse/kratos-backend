"""Admin roster additions reuse team_service with as_admin=True."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.errors import MISSING_REQUIRED_FIELD, TEAM_FULL, AppError
from app.models.enums import (
    MemberRegistrationMode,
    RegistrationFieldScope,
    RegistrationFieldSource,
    RegistrationFieldType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import EventRegistrationRule
from app.models.event_content import EventRegistrationField
from app.models.team import TeamMember
from app.schemas.team import RosterAddRequest
from app.services import admin_ops_service as ops
from app.services import team_service

from tests.conftest import _make_event, _make_team_registration, _make_user_profile, requires_db

pytestmark = requires_db


async def _paid_incomplete_team(db, *, required: int = 6, substitutes: int = 1):
    leader = await _make_user_profile(db, email=f"adm-lead-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Admin Roster {uuid.uuid4().hex[:6]}", team=True)
    rules = (
        await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id))
    ).scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    rules.required_member_count = required
    rules.substitute_count = substitutes
    rules.team_min_size = required
    rules.team_max_size = required + substitutes
    await db.flush()
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    team.status = TeamStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    leader_row = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.profile_id == leader.id,
            )
        )
    ).scalar_one()
    leader_row.status = TeamMemberStatus.ACTIVE
    await db.flush()
    return leader, team, event


def _payload(**kwargs):
    base = {
        "role": TeamMemberRole.MEMBER,
        "full_name": "Admin Added",
        "phone": f"9{uuid.uuid4().int % 10_000_000_000:09d}",
    }
    base.update(kwargs)
    return RosterAddRequest(**base)


@pytest.mark.asyncio
async def test_admin_adds_member_to_forming_team(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    team.status = TeamStatus.FORMING
    await db.flush()
    detail = await ops.admin_add_team_roster_member(db, team.id, _payload())
    assert any(m["full_name"] == "Admin Added" for m in detail["members"])


@pytest.mark.asyncio
async def test_admin_adds_member_to_paid_incomplete_team(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    detail = await ops.admin_add_team_roster_member(db, team.id, _payload())
    assert detail["mandatory_filled"] >= 2


@pytest.mark.asyncio
async def test_admin_add_substitute_when_slot_available(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=1)
    for i in range(2):
        await ops.admin_add_team_roster_member(
            db,
            team.id,
            _payload(full_name=f"M{i}", phone=f"911100000{i:02d}"),
        )
    detail = await ops.admin_add_team_roster_member(
        db,
        team.id,
        RosterAddRequest(role=TeamMemberRole.SUBSTITUTE, full_name="Sub", phone="92220000001"),
    )
    assert detail["substitutes_filled"] == 1
    assert any(m["full_name"] == "Sub" and m["role"] == TeamMemberRole.SUBSTITUTE for m in detail["members"])


@pytest.mark.asyncio
async def test_admin_department_required(db):
    leader, team, event = await _paid_incomplete_team(db, required=3, substitutes=0)
    db.add(
        EventRegistrationField(
            event_id=event.id,
            scope=RegistrationFieldScope.TEAM_MEMBER,
            field_key="department",
            label="Department",
            field_type=RegistrationFieldType.TEXT,
            required=True,
            is_visible=True,
            display_order=1,
            source=RegistrationFieldSource.PROFILE,
            profile_field_key="department",
        )
    )
    await db.flush()
    with pytest.raises(AppError) as exc:
        await ops.admin_add_team_roster_member(db, team.id, _payload(department=None))
    assert exc.value.code == MISSING_REQUIRED_FIELD


@pytest.mark.asyncio
async def test_cancelled_team_rejected(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    team.status = TeamStatus.CANCELLED
    await db.flush()
    with pytest.raises(HTTPException):
        await team_service.add_roster_member(
            db, team.id, leader, _payload(), as_admin=True
        )


@pytest.mark.asyncio
async def test_public_roster_unchanged_signature(db):
    import inspect

    sig = inspect.signature(team_service.add_roster_member)
    assert "as_admin" in sig.parameters
