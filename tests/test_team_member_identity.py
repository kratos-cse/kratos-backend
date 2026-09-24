"""TeamMember.id is the stable roster identity; profile_id remains optional."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import (
    MemberRegistrationMode,
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import EventRegistrationRule
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.registration import RegistrationOut
from app.services import qr_service
from app.services.registration_service import get_registration_or_404
from app.services.team_service import link_member_profile

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db

REAL_TEAM_REGISTRATION_ID = uuid.UUID("0e8e7596-3654-48a0-98cf-9769e7e407f9")
REAL_TEAM_ID = uuid.UUID("5fd7be9b-199d-4e4f-a469-df673f5515f0")
REAL_SECOND_REGISTRATION_ID = uuid.UUID("7421b694-2f93-4712-819d-a8570f44c5ff")


async def _leader_entered_team(db):
    leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@id.test")
    event = await _make_event(db, name=f"Identity {uuid.uuid4().hex[:6]}", team=True)
    rules = (
        await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id))
    ).scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    await db.flush()

    team, registration = await _make_team_registration(db, event=event, leader=leader)
    member = TeamMember(
        team_id=team.id,
        event_id=event.id,
        profile_id=None,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
        entry_source=TeamMemberEntrySource.LEADER_ENTERED,
        full_name="Leader Entered Member",
        phone="9876543210",
    )
    db.add(member)
    await db.flush()
    return leader, team, registration, member


@pytest.mark.asyncio
async def test_leader_entered_member_has_stable_id_and_null_profile(db):
    _leader, _team, registration, member = await _leader_entered_team(db)
    member_id = member.id

    loaded = await get_registration_or_404(db, registration.id)
    out = RegistrationOut.model_validate(loaded)
    entered = next(m for m in out.team.members if m.id == member_id)

    assert entered.id == member_id
    assert entered.profile_id is None
    assert entered.entry_source == TeamMemberEntrySource.LEADER_ENTERED
    assert entered.full_name == "Leader Entered Member"

    loaded_again = await get_registration_or_404(db, registration.id)
    out_again = RegistrationOut.model_validate(loaded_again)
    same = next(m for m in out_again.team.members if m.id == member_id)
    assert same.id == member_id


@pytest.mark.asyncio
async def test_linked_member_has_id_and_profile_id(db):
    leader = await _make_user_profile(db, email=f"linked-{uuid.uuid4().hex}@id.test")
    member_profile = await _make_user_profile(db, email=f"member-{uuid.uuid4().hex}@id.test")
    event = await _make_event(db, name=f"Linked {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    linked = TeamMember(
        team_id=team.id,
        event_id=event.id,
        profile_id=member_profile.id,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
        entry_source=TeamMemberEntrySource.LINKED_ACCOUNT,
    )
    db.add(linked)
    await db.flush()

    out = RegistrationOut.model_validate(await get_registration_or_404(db, registration.id))
    row = next(m for m in out.team.members if m.id == linked.id)
    assert row.profile_id == member_profile.id
    assert row.entry_source == TeamMemberEntrySource.LINKED_ACCOUNT


@pytest.mark.asyncio
async def test_link_profile_preserves_team_member_id(db):
    _leader, _team, _registration, member = await _leader_entered_team(db)
    member_id = member.id
    new_profile = await _make_user_profile(db, email=f"late-{uuid.uuid4().hex}@id.test")

    linked = await link_member_profile(db, member_id, new_profile)
    assert linked.id == member_id
    assert linked.profile_id == new_profile.id
    assert linked.entry_source == TeamMemberEntrySource.LINKED_ACCOUNT


@pytest.mark.asyncio
async def test_qr_generated_for_leader_entered_member_by_team_member_id(db):
    leader, team, registration, member = await _leader_entered_team(db)
    team.status = TeamStatus.PAID
    payment = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )
    registration.status = RegistrationStatus.CONFIRMED
    await db.flush()

    qr = await qr_service.generate_for_team_member(db, member.id)
    await db.flush()

    stored = (
        await db.execute(select(QRCode).where(QRCode.team_member_id == member.id, QRCode.is_active.is_(True)))
    ).scalar_one()
    assert stored.id == qr.id
    assert stored.team_member_id == member.id
    assert stored.registration_id is None
    assert payment.id == registration.payment_id


@pytest.mark.asyncio
async def test_real_registrations_leader_entered_members_have_ids(db):
    for reg_id in (REAL_TEAM_REGISTRATION_ID, REAL_SECOND_REGISTRATION_ID):
        result = await db.execute(
            select(Registration)
            .where(Registration.id == reg_id)
            .options(selectinload(Registration.team).selectinload(Team.members))
        )
        reg = result.scalar_one_or_none()
        if reg is None:
            pytest.skip(f"Registration {reg_id} not in this database")

        out = RegistrationOut.model_validate(reg)
        assert out.team is not None
        for member in out.team.members:
            assert member.id is not None
        leader_entered = [m for m in out.team.members if m.profile_id is None and m.role != TeamMemberRole.LEADER]
        assert leader_entered, "expected leader-entered members in production team"
        ids = {m.id for m in leader_entered}
        assert len(ids) == len(leader_entered)
