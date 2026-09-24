"""Regression tests for production bugs (nullable profile_id, profile PATCH, create-order)."""
import time
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1.endpoints.payments import _link_registration_payment
from app.api.v1.endpoints.profile import update_my_profile
from app.core.security import _profile_cache, invalidate_user_cache
from app.models.enums import (
    MemberRegistrationMode,
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
)
from app.models.event import EventRegistrationRule
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.schemas.profile import ProfileUpdateRequest
from app.schemas.registration import RegistrationOut
from app.schemas.team import TeamMemberOut
from app.services.registration_service import get_registration_or_404, list_my_registrations

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


async def _team_with_leader_entered_members(db, *, member_count: int = 2):
    leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@reg.test")
    event = await _make_event(db, name=f"Leader Entry {uuid.uuid4().hex[:6]}", team=True)
    rules_result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event.id)
    )
    rules = rules_result.scalar_one()
    rules.member_registration_mode = MemberRegistrationMode.LEADER_MANAGED
    rules.registration_mode = RegistrationMode.TEAM_ONLY
    await db.flush()

    team, registration = await _make_team_registration(db, event=event, leader=leader)
    for i in range(member_count):
        db.add(
            TeamMember(
                team_id=team.id,
                event_id=event.id,
                profile_id=None,
                role=TeamMemberRole.MEMBER,
                status=TeamMemberStatus.ACTIVE,
                entry_source=TeamMemberEntrySource.LEADER_ENTERED,
                full_name=f"Member {i + 1}",
                phone=f"90000000{i:02d}",
            )
        )
    await db.flush()
    return leader, event, team, registration


@pytest.mark.asyncio
async def test_team_member_out_allows_null_profile_id():
    member = TeamMemberOut(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        profile_id=None,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
        joined_at=datetime.now(timezone.utc),
        entry_source=TeamMemberEntrySource.LEADER_ENTERED,
        full_name="Leader Entered",
        phone="9000000001",
    )
    assert member.profile_id is None


@pytest.mark.asyncio
async def test_get_registration_serializes_leader_entered_members(db):
    leader, _event, _team, registration = await _team_with_leader_entered_members(db, member_count=3)
    loaded = await get_registration_or_404(db, registration.id)
    out = RegistrationOut.model_validate(loaded)
    assert out.team is not None
    null_profiles = [m for m in out.team.members if m.profile_id is None and m.role != TeamMemberRole.LEADER]
    assert len(null_profiles) == 3
    for member in null_profiles:
        assert member.id is not None
        assert member.entry_source == TeamMemberEntrySource.LEADER_ENTERED


@pytest.mark.asyncio
async def test_list_my_registrations_serializes_leader_entered_members(db):
    leader, _event, _team, registration = await _team_with_leader_entered_members(db, member_count=2)
    rows = await list_my_registrations(db, leader)
    match = next((r for r in rows if r.id == registration.id), None)
    assert match is not None
    out = RegistrationOut.model_validate(match)
    assert any(m.profile_id is None for m in out.team.members)


@pytest.mark.asyncio
async def test_profile_update_with_cached_detached_profile(db):
    profile = await _make_user_profile(db, email=f"patch-{uuid.uuid4().hex}@reg.test")
    _profile_cache[profile.user_id] = (time.monotonic() + 60.0, profile)

    updated = await update_my_profile(
        ProfileUpdateRequest(college_name="Regression College"),
        db=db,
        profile=profile,
    )
    assert updated.college_name == "Regression College"
    invalidate_user_cache(profile.user_id)


@pytest.mark.asyncio
async def test_create_order_rejects_registration_with_existing_payment(db):
    leader = await _make_user_profile(db, email=f"payer-{uuid.uuid4().hex}@reg.test")
    event = await _make_event(db, name=f"Pay Dup {uuid.uuid4().hex[:6]}", team=True)
    _team, registration = await _make_team_registration(db, event=event, leader=leader)
    existing = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
    )
    new_payment = Payment(
        payer_profile_id=leader.id,
        payment_type=PaymentType.TEAM_REGISTRATION,
        razorpay_order_id=f"order_{uuid.uuid4().hex}",
        amount_paise=25000,
        currency="INR",
        status=PaymentStatus.CREATED,
    )
    db.add(new_payment)
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await _link_registration_payment(
            db,
            payment=new_payment,
            event_id=event.id,
            payer=leader,
            payment_type=PaymentType.TEAM_REGISTRATION,
            registration_id=registration.id,
        )
    assert exc.value.status_code == 400
    assert "already has a payment" in exc.value.detail.lower()
    assert registration.payment_id == existing.id


@pytest.mark.asyncio
async def test_real_registrations_remain_intact(db):
    """Read-only guard on the two production registrations when present in this DB."""
    for reg_id, expected_team, expected_payment in (
        (REAL_TEAM_REGISTRATION_ID, REAL_TEAM_ID, uuid.UUID("2b4aa855-c2f9-4fd1-a899-342c5dc52d3b")),
        (REAL_SECOND_REGISTRATION_ID, uuid.UUID("d64f16ae-ab98-4e19-b063-8f93e12bb6d7"), uuid.UUID("3bee1157-7c93-46ca-b998-87bd259d3d7d")),
    ):
        result = await db.execute(
            select(Registration)
            .where(Registration.id == reg_id)
            .options(selectinload(Registration.team).selectinload(Team.members), selectinload(Registration.payment))
        )
        reg = result.scalar_one_or_none()
        if reg is None:
            pytest.skip(f"Registration {reg_id} not in this database")

        assert reg.team_id == expected_team
        assert reg.payment_id == expected_payment
        assert reg.event_id == uuid.UUID("9a85b167-25a8-41d4-9e6b-291c40bfb217")

        member_count = await db.scalar(
            select(func.count(TeamMember.id)).where(TeamMember.team_id == reg.team_id)
        )
        assert member_count and member_count >= 1

        out = RegistrationOut.model_validate(reg)
        assert out.team is not None
        assert any(m.profile_id is None for m in out.team.members)
