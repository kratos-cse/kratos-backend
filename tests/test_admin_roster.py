"""Admin roster additions reuse team_service with as_admin=True."""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps_admin import get_current_active_admin
from app.core.errors import ALREADY_REGISTERED, MISSING_REQUIRED_FIELD, TEAM_FULL, TEAM_MANDATORY_FULL, TEAM_SUBSTITUTE_LIMIT, AppError
from app.main import app
from app.models.admin import ADMIN_ROLE_NAME, EVENT_COORDINATOR_ROLE_NAME
from app.models.qr_code import QRCode
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

from app.schemas.event_content import FieldResponseInput
from tests.conftest import _make_event, _make_team_registration, _make_user_profile, requires_db
from tests.test_event_scoped_rbac import _admin_with_role

pytestmark = requires_db


@pytest.fixture
def api_client():
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


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


@pytest.mark.asyncio
async def test_admin_mandatory_limit_enforced(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93100000001"))
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93100000002"))
    with pytest.raises(AppError) as exc:
        await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93100000003"))
    assert exc.value.code in (TEAM_MANDATORY_FULL, TEAM_FULL)


@pytest.mark.asyncio
async def test_admin_substitute_limit_enforced(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=1)
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93200000001"))
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93200000002"))
    await ops.admin_add_team_roster_member(
        db,
        team.id,
        RosterAddRequest(role=TeamMemberRole.SUBSTITUTE, full_name="Sub", phone="93200000003"),
    )
    with pytest.raises(AppError) as exc:
        await ops.admin_add_team_roster_member(
            db,
            team.id,
            RosterAddRequest(role=TeamMemberRole.SUBSTITUTE, full_name="Sub2", phone="93200000004"),
        )
    assert exc.value.code in (TEAM_SUBSTITUTE_LIMIT, TEAM_FULL)


@pytest.mark.asyncio
async def test_admin_department_persisted(db):
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
    detail = await ops.admin_add_team_roster_member(
        db, team.id, _payload(department="ECE", phone="93300000001")
    )
    row = next(m for m in detail["members"] if m.get("phone") == "93300000001")
    assert row["department"] == "ECE"


@pytest.mark.asyncio
async def test_admin_custom_mcq_persisted(db):
    leader, team, event = await _paid_incomplete_team(db, required=3, substitutes=0)
    field_id = uuid.uuid4()
    db.add(
        EventRegistrationField(
            id=field_id,
            event_id=event.id,
            scope=RegistrationFieldScope.TEAM_MEMBER,
            field_key="gender",
            label="Gender",
            field_type=RegistrationFieldType.MCQ,
            required=True,
            is_visible=True,
            display_order=2,
            source=RegistrationFieldSource.CUSTOM,
            options={"choices": ["Male", "Female", "Other"]},
        )
    )
    await db.flush()
    detail = await ops.admin_add_team_roster_member(
        db,
        team.id,
        _payload(
            phone="93400000001",
            field_responses=[FieldResponseInput(field_id=field_id, value="Male")],
        ),
    )
    member = next(m for m in detail["members"] if m.get("phone") == "93400000001")
    assert any(fr.get("value") == "Male" for fr in member.get("field_responses") or [])


@pytest.mark.asyncio
async def test_admin_duplicate_phone_rejected(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    phone = "93500000001"
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone=phone))
    with pytest.raises(AppError) as exc:
        await ops.admin_add_team_roster_member(db, team.id, _payload(phone=phone, full_name="Dup"))
    assert exc.value.code == ALREADY_REGISTERED


@pytest.mark.asyncio
async def test_admin_leader_entered_member_has_null_profile(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    detail = await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93600000001"))
    member = next(m for m in detail["members"] if m.get("phone") == "93600000001")
    assert member["profile_id"] is None
    assert member["full_name"] == "Admin Added"


@pytest.mark.asyncio
async def test_admin_qr_generated_for_new_member(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    detail = await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93700000001"))
    member_id = next(m["id"] for m in detail["members"] if m.get("phone") == "93700000001")
    count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.team_member_id == member_id)
        )
    ).scalar_one()
    assert count >= 1


@pytest.mark.asyncio
async def test_admin_completes_paid_team_when_mandatory_met(db):
    leader, team, _event = await _paid_incomplete_team(db, required=3, substitutes=0)
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93800000001"))
    await ops.admin_add_team_roster_member(db, team.id, _payload(phone="93800000002"))
    detail = await ops.get_admin_team_detail(db, team.id)
    assert detail["status"] == TeamStatus.COMPLETE
    assert detail["mandatory_filled"] >= 3


def test_http_admin_without_team_edit_receives_403(api_client):
    app.dependency_overrides[get_current_active_admin] = lambda: _admin_with_role(
        ADMIN_ROLE_NAME, ["team-read"]
    )
    resp = api_client.post(
        f"/api/v1/admin/teams/{uuid.uuid4()}/roster",
        json={"role": "MEMBER", "full_name": "X", "phone": "9999999999"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403


def test_http_coordinator_without_team_edit_receives_403(api_client):
    app.dependency_overrides[get_current_active_admin] = lambda: _admin_with_role(
        EVENT_COORDINATOR_ROLE_NAME, ["team-read", "registration-read"]
    )
    resp = api_client.post(
        f"/api/v1/admin/teams/{uuid.uuid4()}/roster",
        json={"role": "MEMBER", "full_name": "X", "phone": "9999999998"},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403
