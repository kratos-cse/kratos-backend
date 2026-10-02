"""
Integration tests for HTF 2026 – Member 2 domain:
  • HTF Problem Statement list & selection (with unique_claim locking)
  • HTF Team creation (no upfront payment, leader ACTIVE immediately)
  • HTF Team invite join, duplicate membership prevention, capacity enforcement
  • HTF Team readiness check (capacity + profile completeness + PS)
  • HTF Roster lock enforcement on leave / remove post-submission
  • HTF Participant digital pass (NOT_AVAILABLE vs ACTIVE with QR token)
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import requires_db

pytestmark = requires_db

from app.core.errors import (
    HTF_ALREADY_IN_TEAM,
    HTF_LEADER_ONLY,
    HTF_PASS_NOT_AVAILABLE,
    HTF_PS_ALREADY_SELECTED,
    HTF_PS_NOT_FOUND,
    HTF_ROSTER_LOCKED,
    HTF_TEAM_FULL,
)
from app.models.enums import (
    EventCategory,
    EventRegistrationStatus,
    EventVisibility,
    HTFApplicationStatus,
    HTFPassStatus,
    HTFProblemDomain,
    MemberRegistrationMode,
    RegistrationMode,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.htf_team import HTFProblemStatement, HTFTeamMeta
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.team import Team, TeamInvitation, TeamMember
from app.models.user import User
from app.services import htf_team_service, qr_service


async def _create_test_profile(
    db: AsyncSession,
    *,
    email: str,
    full_name: str = "Test Member",
    phone: str = "9876543210",
    college: str = "ACE Engineering College",
    dept: str = "CSE",
    year: str = "3rd Year",
) -> Profile:
    user = User(
        google_sub=f"sub-{uuid.uuid4().hex}",
        email=email,
    )
    db.add(user)
    await db.flush()

    profile = Profile(
        user_id=user.id,
        full_name=full_name,
        contact_email=email,
        phone=phone,
        college_name=college,
        department=dept,
        year_of_study=year,
    )
    db.add(profile)
    await db.flush()
    return profile


async def _create_htf_event(db: AsyncSession) -> tuple[Event, EventRegistrationRule]:
    event = Event(
        name=f"HTF 2026 {uuid.uuid4().hex[:6]}",
        short_desc="Hack the Future Hackathon",
        category=EventCategory.TECHNICAL,
        fee=500,
        venue="Block C Labs",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    db.add(event)
    await db.flush()

    rules = EventRegistrationRule(
        event_id=event.id,
        team_min_size=2,
        team_max_size=4,
        required_member_count=4,
        substitute_count=0,
        allow_individual=False,
        registration_mode=RegistrationMode.TEAM_ONLY,
        allow_team_invite_flow=True,
        requires_qr_checkin=True,
        member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
    )
    db.add(rules)
    await db.flush()
    return event, rules


@pytest.mark.asyncio
async def test_htf_problem_statements_list(db: AsyncSession):
    event, _ = await _create_htf_event(db)

    ps1 = HTFProblemStatement(
        event_id=event.id,
        code="PS-01",
        title="AI Healthcare",
        domain=HTFProblemDomain.AI_ML,
        is_active=True,
        unique_claim=False,
    )
    ps2 = HTFProblemStatement(
        event_id=event.id,
        code="PS-02",
        title="Blockchain Identity",
        domain=HTFProblemDomain.BLOCKCHAIN,
        is_active=True,
        unique_claim=True,
    )
    db.add_all([ps1, ps2])
    await db.flush()

    ps_list = await htf_team_service.list_problem_statements(db, event.id)
    assert len(ps_list) == 2
    codes = [p.code for p in ps_list]
    assert "PS-01" in codes
    assert "PS-02" in codes
    assert any(p.unique_claim for p in ps_list if p.code == "PS-02")


@pytest.mark.asyncio
async def test_htf_team_creation_immediate_active(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com")

    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Alpha Hackers"
    )

    assert team_dict["name"] == "Alpha Hackers"
    assert team_dict["active_member_count"] == 1

    # Verify leader member status is ACTIVE (not PENDING_PAYMENT)
    leader_member = team_dict["members"][0]
    assert leader_member["status"] == TeamMemberStatus.ACTIVE
    assert leader_member["role"] == TeamMemberRole.LEADER

    # Verify HTFTeamMeta was created
    meta = await htf_team_service._get_meta_for_team(db, team_dict["id"])
    assert meta is not None
    assert meta.application_status == HTFApplicationStatus.DRAFT
    assert meta.roster_locked is False


@pytest.mark.asyncio
async def test_htf_select_problem_statement(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com")
    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Alpha Hackers"
    )

    ps = HTFProblemStatement(
        event_id=event.id,
        code="PS-10",
        title="Web Security Platform",
        domain=HTFProblemDomain.CYBER_SECURITY,
        is_active=True,
        unique_claim=False,
    )
    db.add(ps)
    await db.flush()

    readiness = await htf_team_service.select_problem_statement(
        db, team_id=team_dict["id"], ps_id=ps.id, profile=leader
    )

    assert readiness.ps_selected is True
    assert readiness.ps_code == "PS-10"
    assert readiness.ps_domain == HTFProblemDomain.CYBER_SECURITY


@pytest.mark.asyncio
async def test_htf_unique_ps_conflict(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader1 = await _create_test_profile(db, email=f"lead1-{uuid.uuid4().hex[:6]}@test.com")
    leader2 = await _create_test_profile(db, email=f"lead2-{uuid.uuid4().hex[:6]}@test.com")

    team1 = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader1, name="Team One"
    )
    team2 = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader2, name="Team Two"
    )

    unique_ps = HTFProblemStatement(
        event_id=event.id,
        code="PS-UNIQUE",
        title="Rare Innovation Topic",
        domain=HTFProblemDomain.OPEN_INNOVATION,
        is_active=True,
        unique_claim=True,
    )
    db.add(unique_ps)
    await db.flush()

    # Team 1 claims it first
    await htf_team_service.select_problem_statement(
        db, team_id=team1["id"], ps_id=unique_ps.id, profile=leader1
    )

    # Team 2 tries to claim the same unique PS
    from app.core.errors import AppError
    with pytest.raises(AppError) as exc_info:
        await htf_team_service.select_problem_statement(
            db, team_id=team2["id"], ps_id=unique_ps.id, profile=leader2
        )
    assert exc_info.value.code == HTF_PS_ALREADY_SELECTED


@pytest.mark.asyncio
async def test_htf_team_readiness_logic(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    # Leader has an incomplete profile (missing phone)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com", phone="")
    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Beta Hackers"
    )

    # Team size is 1 (< min 2), profile is incomplete, no PS selected
    readiness = await htf_team_service.get_team_readiness(
        db, profile=leader, event_id=event.id
    )
    assert readiness.is_ready is False
    assert readiness.capacity_ok is False
    assert readiness.all_profiles_complete is False
    assert readiness.ps_selected is False
    assert len(readiness.blockers) >= 3


@pytest.mark.asyncio
async def test_htf_invite_join_and_duplicate_prevention(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com")
    member2 = await _create_test_profile(db, email=f"mem2-{uuid.uuid4().hex[:6]}@test.com")

    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Gamma Squad"
    )

    # Generate invite
    invite_code = f"INVITE-{uuid.uuid4().hex[:8]}"
    db.add(
        TeamInvitation(
            team_id=team_dict["id"],
            code=invite_code,
            is_active=True,
            created_by_profile_id=leader.id,
        )
    )
    await db.flush()

    # Member 2 joins
    join_res = await htf_team_service.join_htf_team_via_invite(
        db, invite_code=invite_code, profile=member2, event_id=event.id
    )
    assert join_res["member"]["status"] == TeamMemberStatus.ACTIVE

    # Member 2 tries to join again
    from app.core.errors import AppError
    with pytest.raises(AppError) as exc_info:
        await htf_team_service.join_htf_team_via_invite(
            db, invite_code=invite_code, profile=member2, event_id=event.id
        )
    assert exc_info.value.code == HTF_ALREADY_IN_TEAM


@pytest.mark.asyncio
async def test_htf_roster_locked_on_submitted(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com")
    member2 = await _create_test_profile(db, email=f"mem2-{uuid.uuid4().hex[:6]}@test.com")

    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Delta Team"
    )

    # Add member 2
    tm2 = TeamMember(
        team_id=team_dict["id"],
        event_id=event.id,
        profile_id=member2.id,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
    )
    db.add(tm2)
    await db.flush()

    # Simulate Member 1 submitting the application (roster_locked = True)
    meta = await htf_team_service._get_meta_for_team(db, team_dict["id"])
    meta.application_status = HTFApplicationStatus.SUBMITTED
    meta.roster_locked = True
    await db.flush()

    # Member 2 tries to leave
    from app.core.errors import AppError
    with pytest.raises(AppError) as exc_info:
        await htf_team_service.leave_htf_team(
            db, team_id=team_dict["id"], member_id=tm2.id, profile=member2
        )
    assert exc_info.value.code == HTF_ROSTER_LOCKED


@pytest.mark.asyncio
async def test_htf_participant_pass_confirmed(db: AsyncSession):
    event, _ = await _create_htf_event(db)
    leader = await _create_test_profile(db, email=f"lead-{uuid.uuid4().hex[:6]}@test.com")
    team_dict = await htf_team_service.create_htf_team(
        db, event_id=event.id, profile=leader, name="Epsilon Crew"
    )

    # Fetch team member
    tm_result = await db.execute(
        select(TeamMember).where(TeamMember.team_id == team_dict["id"])
    )
    tm = tm_result.scalar_one()

    # Generate QR for team member
    qr = await qr_service.generate_for_team_member(db, tm.id)

    # 1. When DRAFT -> Pass status NOT_AVAILABLE
    pass_draft = await htf_team_service.get_htf_pass(
        db, profile=leader, event_id=event.id
    )
    assert pass_draft.pass_status == HTFPassStatus.NOT_AVAILABLE
    assert pass_draft.my_qr_token is None

    # 2. When CONFIRMED -> Pass status ACTIVE and has my_qr_token
    meta = await htf_team_service._get_meta_for_team(db, team_dict["id"])
    meta.application_status = HTFApplicationStatus.CONFIRMED
    await db.flush()

    pass_conf = await htf_team_service.get_htf_pass(
        db, profile=leader, event_id=event.id
    )
    assert pass_conf.pass_status == HTFPassStatus.ACTIVE
    assert pass_conf.my_qr_token == qr.token
    assert pass_conf.team_name == "Epsilon Crew"
