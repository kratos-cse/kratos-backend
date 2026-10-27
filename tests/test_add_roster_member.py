"""Leader-entered roster POST /teams/{id}/roster — fields, limits, PAID teams."""
import uuid

import pytest
from sqlalchemy import select

from unittest.mock import AsyncMock, patch

from app.core.errors import (
    ALREADY_REGISTERED,
    MISSING_REQUIRED_FIELD,
    MSG_DUPLICATE_PHONE_ON_EVENT,
    MSG_ROSTER_INVALID_SUBSTITUTE,
    MSG_SUBSTITUTE_SLOTS_FULL,
    MSG_TEAM_ROSTER_FULL,
    ROSTER_INVALID,
    TEAM_FULL,
    TEAM_SUBSTITUTE_LIMIT,
    AppError,
)
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
from app.services.roster_service import apply_roster_to_rules

from tests.conftest import (
    _make_event,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

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
    assert exc.value.code == TEAM_FULL
    assert exc.value.message == MSG_TEAM_ROSTER_FULL


@pytest.mark.asyncio
async def test_add_roster_substitute_before_mandatory_met_returns_substitute_limit(db):
    """MEMBERS_SUBSTITUTES: substitute role is rejected until mandatory count is met."""
    leader, team, _event, _rules = await _leader_managed_paid_team(db, required=5, substitutes=2)
    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            RosterAddRequest(
                role=TeamMemberRole.SUBSTITUTE,
                full_name="Sub Too Early",
                phone="91100000001",
            ),
        )
    assert exc.value.code == TEAM_SUBSTITUTE_LIMIT
    assert exc.value.message == MSG_SUBSTITUTE_SLOTS_FULL


@pytest.mark.asyncio
async def test_range_substitute_rejected_before_mandatory_filled(db):
    leader = await _make_user_profile(db, email=f"range-sub-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"RangeSub {uuid.uuid4().hex[:6]}", team=True)
    rules = (
        await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id))
    ).scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    apply_roster_to_rules(rules, roster_style_value="RANGE", team_min_size=3, team_max_size=4)
    await db.flush()

    team, _registration = await _make_team_registration(db, event=event, leader=leader)
    team.status = TeamStatus.PAID

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            RosterAddRequest(
                role=TeamMemberRole.SUBSTITUTE,
                full_name="Wrong Role",
                phone="97000000001",
            ),
        )
    assert exc.value.code == ROSTER_INVALID
    assert exc.value.message == MSG_ROSTER_INVALID_SUBSTITUTE


@pytest.mark.asyncio
async def test_range_substitute_rejected_when_optional_slot_open(db):
    leader = await _make_user_profile(db, email=f"range-sub2-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"RangeSub2 {uuid.uuid4().hex[:6]}", team=True)
    rules = (
        await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id))
    ).scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    apply_roster_to_rules(rules, roster_style_value="RANGE", team_min_size=3, team_max_size=4)
    await db.flush()

    team, _registration = await _make_team_registration(db, event=event, leader=leader)
    team.status = TeamStatus.PAID
    for i in range(2):
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(full_name=f"M{i}", phone=f"971000000{i:02d}"),
        )

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            RosterAddRequest(
                role=TeamMemberRole.SUBSTITUTE,
                full_name="Sub Not Allowed",
                phone="97100000099",
            ),
        )
    assert exc.value.code == ROSTER_INVALID
    assert exc.value.message == MSG_ROSTER_INVALID_SUBSTITUTE


@pytest.mark.asyncio
async def test_add_roster_duplicate_phone_is_already_registered(db):
    leader, team, _event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
    phone = "98000000001"
    await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(phone=phone, full_name="First"),
    )
    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(phone=phone, full_name="Duplicate"),
        )
    assert exc.value.code == ALREADY_REGISTERED
    assert exc.value.message == MSG_DUPLICATE_PHONE_ON_EVENT


@pytest.mark.asyncio
async def test_add_roster_commit_failure_reraises_not_409(db):
    leader, team, _event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
    payload = _member_payload(phone="99000000001")

    with patch.object(db, "commit", new_callable=AsyncMock) as mock_commit:
        mock_commit.side_effect = RuntimeError("simulated commit failure")
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await team_service.add_roster_member(db, team.id, leader, payload)


@pytest.mark.asyncio
async def test_range_min3_max4_allows_fourth_member_then_rejects_fifth(db):
    leader = await _make_user_profile(db, email=f"range34-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Range34 {uuid.uuid4().hex[:6]}", team=True)
    rules = (
        await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id))
    ).scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    apply_roster_to_rules(rules, roster_style_value="RANGE", team_min_size=3, team_max_size=4)
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

    for i in range(3):
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(full_name=f"M{i}", phone=f"960000000{i:02d}"),
        )

    detail = await team_service.get_team(db, team.id, leader)
    assert detail["team_max_size"] == 4
    assert detail["active_member_count"] == 4

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(full_name="TooMany", phone="96000000099"),
        )
    assert exc.value.code == TEAM_FULL
    assert exc.value.message == MSG_TEAM_ROSTER_FULL


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


@pytest.mark.asyncio
async def test_add_roster_duplicate_profile_phone_multiple_rows_returns_409(db):
    """Two linked members with profiles sharing phone must not raise MultipleResultsFound."""
    shared_phone = "98111111111"
    leader, team, event, _rules = await _leader_managed_paid_team(db, required=6, substitutes=0)

    other_leader = await _make_user_profile(db, email=f"other-lead-{uuid.uuid4().hex}@t.local")
    other_leader.phone = shared_phone
    await db.flush()

    other_team, _ = await _make_team_registration(db, event=event, leader=other_leader)
    other_team.status = TeamStatus.PAID
    other_leader_row = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == other_team.id,
                TeamMember.profile_id == other_leader.id,
            )
        )
    ).scalar_one()
    other_leader_row.status = TeamMemberStatus.ACTIVE
    await db.flush()

    third_leader = await _make_user_profile(db, email=f"third-lead-{uuid.uuid4().hex}@t.local")
    third_leader.phone = shared_phone
    await db.flush()
    third_team, _ = await _make_team_registration(db, event=event, leader=third_leader)
    third_team.status = TeamStatus.PAID
    third_leader_row = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == third_team.id,
                TeamMember.profile_id == third_leader.id,
            )
        )
    ).scalar_one()
    third_leader_row.status = TeamMemberStatus.ACTIVE
    await db.flush()

    with pytest.raises(AppError) as exc:
        await team_service.add_roster_member(
            db,
            team.id,
            leader,
            _member_payload(phone=shared_phone, full_name="Should Conflict"),
        )
    assert exc.value.code == ALREADY_REGISTERED
    assert exc.value.message == MSG_DUPLICATE_PHONE_ON_EVENT


@pytest.mark.asyncio
async def test_add_roster_terminal_member_phone_does_not_block(db):
    leader, team, _event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
    phone = "98222222222"
    await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(phone=phone, full_name="Former"),
    )
    former = (
        await db.execute(
            select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.phone == phone)
        )
    ).scalar_one()
    former.status = TeamMemberStatus.LEFT
    await db.flush()

    detail = await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(phone=phone, full_name="Replacement"),
    )
    assert any(m["phone"] == phone and m["full_name"] == "Replacement" for m in detail["members"])


@pytest.mark.asyncio
async def test_add_roster_same_phone_different_event_allowed(db):
    leader, team, event, _rules = await _leader_managed_paid_team(db, required=3, substitutes=0)
    phone = "98333333333"
    await team_service.add_roster_member(
        db,
        team.id,
        leader,
        _member_payload(phone=phone, full_name="On Event A"),
    )

    other_event = await _make_event(db, name=f"Other {uuid.uuid4().hex[:6]}", team=True)
    rules_b = (
        await db.execute(
            select(EventRegistrationRule).where(EventRegistrationRule.event_id == other_event.id)
        )
    ).scalar_one()
    rules_b.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules_b.registration_mode = RegistrationMode.TEAM_ONLY
    rules_b.required_member_count = 3
    rules_b.team_min_size = 3
    rules_b.team_max_size = 3
    await db.flush()

    leader_b = await _make_user_profile(db, email=f"lead-b-{uuid.uuid4().hex}@t.local")
    team_b, reg_b = await _make_team_registration(db, event=other_event, leader=leader_b)
    team_b.status = TeamStatus.PAID
    reg_b.status = RegistrationStatus.CONFIRMED
    leader_row_b = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team_b.id,
                TeamMember.profile_id == leader_b.id,
            )
        )
    ).scalar_one()
    leader_row_b.status = TeamMemberStatus.ACTIVE
    await db.flush()

    detail = await team_service.add_roster_member(
        db,
        team_b.id,
        leader_b,
        _member_payload(phone=phone, full_name="On Event B"),
    )
    assert any(m["phone"] == phone for m in detail["members"])
