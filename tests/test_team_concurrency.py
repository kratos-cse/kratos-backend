"""
True concurrency integration tests (separate DB sessions per task).

Invariants rely on PostgreSQL row-level locks:
- join_via_invitation / add_roster_member: SELECT Team FOR UPDATE before counting members
- create_registration: event row FOR UPDATE via _get_event_with_rules(for_update=True)
- partial unique indexes enforce one active registration/membership per event+profile
"""
import asyncio
import secrets
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.errors import ALREADY_REGISTERED, AppError, TEAM_FULL
from app.models.enums import (
    CapacityType,
    EventCategory,
    EventRegistrationStatus,
    EventVisibility,
    MemberRegistrationMode,
    PaymentStatus,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamInvitation, TeamMember
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services import team_service
from app.services.registration_service import create_registration
from tests.conftest import _make_user_profile, asyncpg_connect_args, requires_db

pytestmark = requires_db


@pytest.fixture
def concurrent_engine():
    return create_async_engine(
        settings.async_database_url,
        poolclass=NullPool,
        connect_args=asyncpg_connect_args(),
    )


def _session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _team_event_committed(session_factory, *, capacity: int = 50, required: int = 2):
    async with session_factory() as db:
        event = Event(
            name=f"TeamCap {uuid.uuid4().hex[:6]}",
            short_desc="Test",
            category=EventCategory.TECHNICAL,
            fee=0,
            venue="Hall",
            visibility=EventVisibility.PUBLISHED,
            registration_status=EventRegistrationStatus.OPEN,
            capacity=capacity,
        )
        db.add(event)
        await db.flush()
        db.add(
            EventRegistrationRule(
                event_id=event.id,
                team_min_size=required,
                team_max_size=required,
                required_member_count=required,
                substitute_count=0,
                allow_individual=False,
                registration_mode=RegistrationMode.TEAM_ONLY,
                capacity_type=CapacityType.PARTICIPANTS,
                member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
                allow_team_invite_flow=True,
            )
        )
        await db.commit()
        return event.id


@pytest.mark.asyncio
async def test_concurrent_team_join_one_slot(concurrent_engine):
    """Team has one remaining slot — exactly one concurrent join succeeds."""
    session_factory = _session_factory(concurrent_engine)
    event_id = await _team_event_committed(session_factory, required=2)

    async with session_factory() as db:
        leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@t.local")
        member_a = await _make_user_profile(db, email=f"a-{uuid.uuid4().hex}@t.local")
        member_b = await _make_user_profile(db, email=f"b-{uuid.uuid4().hex}@t.local")
        team = Team(
            event_id=event_id,
            name="Full Team",
            leader_profile_id=leader.id,
            status=TeamStatus.PAID,
        )
        db.add(team)
        await db.flush()
        db.add(
            TeamMember(
                team_id=team.id,
                event_id=event_id,
                profile_id=leader.id,
                role=TeamMemberRole.LEADER,
                status=TeamMemberStatus.ACTIVE,
            )
        )
        code = secrets.token_urlsafe(12)
        db.add(
            TeamInvitation(
                team_id=team.id,
                code=code,
                is_active=True,
                created_by_profile_id=leader.id,
            )
        )
        team_id = team.id
        await db.commit()

    async def attempt_join(profile_id: uuid.UUID):
        async with session_factory() as db:
            prof = await db.get(Profile, profile_id)
            try:
                await team_service.join_via_invitation(db, code, prof)
                return "ok"
            except AppError as exc:
                return exc.code

    results = await asyncio.gather(
        attempt_join(member_a.id),
        attempt_join(member_b.id),
    )
    assert sorted(results) == sorted(["ok", TEAM_FULL])

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
            )
        )
        assert count == 2


@pytest.mark.asyncio
async def test_concurrent_event_capacity_one_slot(concurrent_engine):
    session_factory = _session_factory(concurrent_engine)

    async with session_factory() as db:
        event = Event(
            name=f"Cap1 {uuid.uuid4().hex[:6]}",
            short_desc="Test",
            category=EventCategory.TECHNICAL,
            fee=0,
            venue="Hall",
            visibility=EventVisibility.PUBLISHED,
            registration_status=EventRegistrationStatus.OPEN,
            capacity=1,
        )
        db.add(event)
        await db.flush()
        db.add(
            EventRegistrationRule(
                event_id=event.id,
                team_min_size=1,
                team_max_size=1,
                required_member_count=1,
                substitute_count=0,
                allow_individual=True,
                registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
                capacity_type=CapacityType.PARTICIPANTS,
                member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
            )
        )
        await db.flush()
        profile_a = await _make_user_profile(db, email=f"a-{uuid.uuid4().hex}@t.local")
        profile_b = await _make_user_profile(db, email=f"b-{uuid.uuid4().hex}@t.local")
        event_id = event.id
        await db.commit()

    async def attempt_register(profile_id: uuid.UUID):
        async with session_factory() as db:
            prof = await db.get(Profile, profile_id)
            try:
                await create_registration(
                    db,
                    event_id,
                    prof,
                    RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
                )
                return "ok"
            except HTTPException as exc:
                return exc.status_code
            except AppError as exc:
                return exc.code

    results = await asyncio.gather(
        attempt_register(profile_a.id),
        attempt_register(profile_b.id),
    )
    assert results.count("ok") == 1
    assert 400 in results or "CAPACITY_FULL" in str(results)

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(
                Registration.event_id == event_id,
                Registration.status != RegistrationStatus.CANCELLED,
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_invite_acceptance(concurrent_engine):
    session_factory = _session_factory(concurrent_engine)
    event_id = await _team_event_committed(session_factory, required=3)

    async with session_factory() as db:
        leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@t.local")
        member = await _make_user_profile(db, email=f"m-{uuid.uuid4().hex}@t.local")
        team = Team(
            event_id=event_id,
            name="Invite Team",
            leader_profile_id=leader.id,
            status=TeamStatus.PAID,
        )
        db.add(team)
        await db.flush()
        db.add(
            TeamMember(
                team_id=team.id,
                event_id=event_id,
                profile_id=leader.id,
                role=TeamMemberRole.LEADER,
                status=TeamMemberStatus.ACTIVE,
            )
        )
        code = secrets.token_urlsafe(12)
        db.add(
            TeamInvitation(
                team_id=team.id,
                code=code,
                is_active=True,
                created_by_profile_id=leader.id,
            )
        )
        team_id = team.id
        member_id = member.id
        await db.commit()

    async def accept():
        async with session_factory() as db:
            prof = await db.get(Profile, member_id)
            return await team_service.join_via_invitation(db, code, prof)

    await asyncio.gather(accept(), accept())

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.profile_id == member_id,
                TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
            )
        )
        assert count == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_registration(concurrent_engine):
    session_factory = _session_factory(concurrent_engine)

    async with session_factory() as db:
        event = Event(
            name=f"DupReg {uuid.uuid4().hex[:6]}",
            short_desc="Test",
            category=EventCategory.TECHNICAL,
            fee=0,
            venue="Hall",
            visibility=EventVisibility.PUBLISHED,
            registration_status=EventRegistrationStatus.OPEN,
            capacity=50,
        )
        db.add(event)
        await db.flush()
        db.add(
            EventRegistrationRule(
                event_id=event.id,
                team_min_size=1,
                team_max_size=1,
                required_member_count=1,
                substitute_count=0,
                allow_individual=True,
                registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
                capacity_type=CapacityType.PARTICIPANTS,
                member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
            )
        )
        profile = await _make_user_profile(db, email=f"solo-{uuid.uuid4().hex}@t.local")
        event_id = event.id
        profile_id = profile.id
        await db.commit()

    async def attempt():
        async with session_factory() as db:
            prof = await db.get(Profile, profile_id)
            try:
                await create_registration(
                    db,
                    event_id,
                    prof,
                    RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
                )
                return "ok"
            except AppError as exc:
                return exc.code

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == sorted(["ok", ALREADY_REGISTERED])


@pytest.mark.asyncio
async def test_inactive_invite_cannot_be_consumed(concurrent_engine):
    session_factory = _session_factory(concurrent_engine)
    event_id = await _team_event_committed(session_factory, required=2)

    async with session_factory() as db:
        leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@t.local")
        member = await _make_user_profile(db, email=f"m-{uuid.uuid4().hex}@t.local")
        team = Team(
            event_id=event_id,
            name="Revoked",
            leader_profile_id=leader.id,
            status=TeamStatus.PAID,
        )
        db.add(team)
        await db.flush()
        db.add(
            TeamMember(
                team_id=team.id,
                event_id=event_id,
                profile_id=leader.id,
                role=TeamMemberRole.LEADER,
                status=TeamMemberStatus.ACTIVE,
            )
        )
        code = secrets.token_urlsafe(12)
        invite = TeamInvitation(
            team_id=team.id,
            code=code,
            is_active=False,
            created_by_profile_id=leader.id,
        )
        db.add(invite)
        await db.commit()

    async with session_factory() as db:
        prof = await db.get(Profile, member.id)
        with pytest.raises(HTTPException) as exc:
            await team_service.join_via_invitation(db, code, prof)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_concurrent_remove_and_join_preserves_capacity(concurrent_engine):
    """Member removal and join at capacity boundary — roster never exceeds limit."""
    session_factory = _session_factory(concurrent_engine)
    event_id = await _team_event_committed(session_factory, required=2)

    async with session_factory() as db:
        leader = await _make_user_profile(db, email=f"lead-{uuid.uuid4().hex}@t.local")
        member_out = await _make_user_profile(db, email=f"out-{uuid.uuid4().hex}@t.local")
        joiner = await _make_user_profile(db, email=f"in-{uuid.uuid4().hex}@t.local")
        team = Team(
            event_id=event_id,
            name="Race Team",
            leader_profile_id=leader.id,
            status=TeamStatus.PAID,
        )
        db.add(team)
        await db.flush()
        leader_row = TeamMember(
            team_id=team.id,
            event_id=event_id,
            profile_id=leader.id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.ACTIVE,
        )
        member_row = TeamMember(
            team_id=team.id,
            event_id=event_id,
            profile_id=member_out.id,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        )
        db.add(leader_row)
        db.add(member_row)
        code = secrets.token_urlsafe(12)
        db.add(
            TeamInvitation(
                team_id=team.id,
                code=code,
                is_active=True,
                created_by_profile_id=leader.id,
            )
        )
        team_id = team.id
        member_row_id = member_row.id
        leader_id = leader.id
        joiner_id = joiner.id
        await db.commit()

    async def remove_member():
        async with session_factory() as db:
            leader_prof = await db.get(Profile, leader_id)
            await team_service.remove_member(db, team_id, member_row_id, leader_prof)

    async def join_team():
        async with session_factory() as db:
            prof = await db.get(Profile, joiner_id)
            try:
                await team_service.join_via_invitation(db, code, prof)
                return "ok"
            except AppError as exc:
                return exc.code

    await asyncio.gather(remove_member(), join_team())

    async with session_factory() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.status.notin_([TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED]),
            )
        )
        assert count == 2
