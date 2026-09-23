"""Shared DB fixtures for integration tests (requires DATABASE_URL)."""
import os

# Ensure tests never depend on a blank JWT secret from a local .env override.
os.environ.setdefault("JWT_SECRET_KEY", "dev-secret-change-me")

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.models.enums import (
    CapacityType,
    EventCategory,
    EventStatus,
    MemberRegistrationMode,
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.models.user import User

DATABASE_URL = (settings.DATABASE_URL or os.getenv("DATABASE_URL") or "").strip()
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def asyncpg_connect_args() -> dict:
    """Railway public TCP proxy (proxy.rlwy.net) is plain TCP — no TLS on the wire."""
    args: dict = {"timeout": 30, "command_timeout": 60}
    if "proxy.rlwy.net" in (settings.DATABASE_URL or ""):
        args["ssl"] = False
    return args


@pytest_asyncio.fixture
async def db():
    """Yield a session; commits inside tests become savepoints, rolled back after."""
    from sqlalchemy.pool import NullPool

    test_engine = create_async_engine(
        settings.async_database_url,
        poolclass=NullPool,
        connect_args=asyncpg_connect_args(),
    )
    try:
        async with test_engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(
                bind=conn,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await test_engine.dispose()


async def _make_user_profile(db: AsyncSession, *, email: str) -> Profile:
    user = User(
        google_sub=f"sub-{uuid.uuid4().hex}",
        email=email,
    )
    db.add(user)
    await db.flush()
    profile = Profile(
        user_id=user.id,
        full_name="Test Participant",
        contact_email=email,
        college_name="Test College",
    )
    db.add(profile)
    await db.flush()
    return profile


async def _make_event(db: AsyncSession, *, name: str, team: bool = False) -> Event:
    event = Event(
        name=name,
        short_desc="Test event",
        category=EventCategory.TECHNICAL,
        fee=250,
        venue="Test Hall",
        status=EventStatus.OPEN,
    )
    db.add(event)
    await db.flush()
    db.add(
        EventRegistrationRule(
            event_id=event.id,
            team_min_size=2 if team else 1,
            team_max_size=6 if team else 1,
            required_member_count=2 if team else 1,
            substitute_count=0,
            allow_individual=not team,
            registration_mode=RegistrationMode.TEAM_ONLY if team else RegistrationMode.INDIVIDUAL_ONLY,
            capacity_type=CapacityType.TEAMS if team else CapacityType.PARTICIPANTS,
            member_registration_mode=MemberRegistrationMode.SELF_ENTRY,
        )
    )
    await db.flush()
    return event


async def _make_solo_registration(
    db: AsyncSession, *, event: Event, profile: Profile
) -> Registration:
    registration = Registration(
        event_id=event.id,
        profile_id=profile.id,
        status=RegistrationStatus.PENDING,
    )
    db.add(registration)
    await db.flush()
    return registration


async def _make_team_registration(
    db: AsyncSession, *, event: Event, leader: Profile
) -> tuple[Team, Registration]:
    team = Team(
        event_id=event.id,
        name="Test Team",
        leader_profile_id=leader.id,
        status=TeamStatus.FORMING,
    )
    db.add(team)
    await db.flush()
    db.add(
        TeamMember(
            team_id=team.id,
            event_id=event.id,
            profile_id=leader.id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.PENDING_PAYMENT,
        )
    )
    registration = Registration(
        event_id=event.id,
        team_id=team.id,
        status=RegistrationStatus.PENDING,
    )
    db.add(registration)
    await db.flush()
    return team, registration


async def _make_payment(
    db: AsyncSession,
    *,
    payer: Profile,
    payment_type: PaymentType,
    registration: Registration | None = None,
    status: PaymentStatus = PaymentStatus.CREATED,
) -> Payment:
    payment = Payment(
        payer_profile_id=payer.id,
        payment_type=payment_type,
        razorpay_order_id=f"order_{uuid.uuid4().hex}",
        amount_paise=25000,
        currency="INR",
        status=status,
    )
    db.add(payment)
    await db.flush()
    if registration is not None:
        registration.payment_id = payment.id
        await db.flush()
    return payment


@pytest_asyncio.fixture
async def solo_payment_setup(db: AsyncSession):
    profile = await _make_user_profile(db, email=f"solo-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Solo Event {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
    )
    return {"profile": profile, "event": event, "registration": registration, "payment": payment}


@pytest_asyncio.fixture
async def team_payment_setup(db: AsyncSession):
    leader = await _make_user_profile(db, email=f"leader-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Team Event {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    payment = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
    )
    return {
        "leader": leader,
        "event": event,
        "team": team,
        "registration": registration,
        "payment": payment,
    }


async def refresh_registration(db: AsyncSession, registration_id: uuid.UUID) -> Registration:
    result = await db.execute(select(Registration).where(Registration.id == registration_id))
    return result.scalar_one()
