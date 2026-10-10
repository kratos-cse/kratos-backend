"""Leader-entered roster POST /teams/{id}/roster — fields, limits, PAID teams."""
import uuid

import pytest
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
from app.services import team_service

from tests.conftest import _make_event, _make_team_registration, _make_user_profile, requires_db

pytestmark = requires_db


async def _leader_managed_paid_team(db, *, required: int = 6, substitutes: int = 1):
    leader = await _make_user_profile(db, email=f"roster-lead-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Roster {uuid.uuid4().hex[:6]}", team=True)
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
    return leader, team, event, rules


def _member_payload(**overrides):
    base = {
        "role": TeamMemberRole.MEMBER,
        "full_name": "Test Member",
        "phone": f"9{uuid.uuid4().int % 10_000_000_000:09d}",
        "contact_email": f"m-{uuid.uuid4().hex[:8]}@example.com",
        "college_name": "Test College",
        "year_of_study": "2",
    }
    base.update(overrides)
    return RosterAddRequest(**base)


@pytest.mark.asyncio
async def test_add_roster_member_after_payment_succeeds(db):
    leader, team, _event, _rules = await _leader_managed_paid_team(db)
    payload = _member_payload()
    detail = await team_service.add_roster_member(db, team.id, leader, payload)
    assert detail["members"]
    added = next(m for m in detail["members"] if m["phone"] == payload.phone)
    assert added["full_name"] == "Test Member"
    assert added["role"] == TeamMemberRole.MEMBER


@pytest.mark.asyncio
async def test_add_roster_fills_mandatory_then_substitute_then_rejects(db):
    leader, team, _event, rules = await _leader_managed_paid_team(db, required=6, substitutes=1)
    # Leader counts as 1 mandatory — need 5 more members + 1 substitute.
    for i in range(5):
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(full_name=f"M{i}", phone=f"910000000{i:02d}"),
        )

    await team_service.add_roster_member(
        db,
        team.id,
        leader,
        RosterAddRequest(
            role=TeamMemberRole.SUBSTITUTE,
            full_name="Sub One",
            phone="92000000001",
        ),
    )

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(full_name="Too Many", phone="93000000001"),
        )
    assert exc.value.code in (TEAM_FULL, "TEAM_FULL")


@pytest.mark.asyncio
async def test_required_profile_department_on_leader_entered_member(db):
    leader, team, event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
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
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(department=None, phone="94000000001"),
        )
    assert exc.value.code == MISSING_REQUIRED_FIELD
    assert "Department" in exc.value.message

    detail = await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(department="CSE", phone="94000000002"),
    )
    member = next(m for m in detail["members"] if m["phone"] == "94000000002")
    assert member["department"] == "CSE"


@pytest.mark.asyncio
async def test_required_custom_gender_mcq(db):
    leader, team, event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
    gender_field_id = uuid.uuid4()
    db.add(
        EventRegistrationField(
            id=gender_field_id,
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

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(phone="95000000001", field_responses=[]),
        )
    assert exc.value.code == MISSING_REQUIRED_FIELD
    assert "Gender" in exc.value.message

    from app.schemas.event_content import FieldResponseInput

    detail = await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(
            phone="95000000002",
            field_responses=[FieldResponseInput(field_id=gender_field_id, value="Male")],
        ),
    )
    assert any(m["phone"] == "95000000002" for m in detail["members"])
