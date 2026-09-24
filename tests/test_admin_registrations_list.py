"""GET /admin/registrations list payload — registration type and eager loads."""
import uuid

import pytest
from sqlalchemy import select

from app.models.enums import PaymentStatus, PaymentType, TeamMemberRole, TeamMemberStatus
from app.models.registration import Registration
from app.models.team import TeamMember
from app.services.admin_ops_service import (
    ADMIN_REGISTRATION_LIST_LOAD,
    serialize_admin_registration,
)

from .conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)


@requires_db
@pytest.mark.asyncio
async def test_serialize_solo_registration(db):
    profile = await _make_user_profile(db, email=f"solo-list-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Solo List {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.CREATED,
    )

    result = await db.execute(
        select(Registration)
        .where(Registration.id == registration.id)
        .options(*ADMIN_REGISTRATION_LIST_LOAD)
    )
    reg = result.scalar_one()
    payload = serialize_admin_registration(reg)

    assert payload["registration_type"] == "SOLO"
    assert payload["profile_id"] == profile.id
    assert payload["team_id"] is None
    assert payload["team"] is None
    assert payload["payment_id"] == payment.id
    assert payload["payment_status"] == PaymentStatus.CREATED
    assert payload["payment"]["id"] == payment.id
    assert payload["payment"]["amount_paise"] == payment.amount_paise
    assert payload["status"] == registration.status
    assert payload["event_id"] == event.id


@requires_db
@pytest.mark.asyncio
async def test_serialize_team_registration_with_roster(db):
    leader = await _make_user_profile(db, email=f"team-list-{uuid.uuid4().hex}@test.local")
    member = await _make_user_profile(db, email=f"team-mem-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Team List {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    db.add(
        TeamMember(
            team_id=team.id,
            event_id=event.id,
            profile_id=member.id,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    await db.flush()

    result = await db.execute(
        select(Registration)
        .where(Registration.id == registration.id)
        .options(*ADMIN_REGISTRATION_LIST_LOAD)
    )
    reg = result.scalar_one()
    payload = serialize_admin_registration(reg)

    assert payload["registration_type"] == "TEAM"
    assert payload["team_id"] == team.id
    assert payload["profile_id"] is None
    assert payload["team"] is not None
    assert payload["team"]["id"] == team.id
    assert payload["team"]["name"] == team.name
    assert payload["team"]["required_member_count"] == 2
    assert payload["team"]["mandatory_filled"] == 2
    assert payload["team"]["active_member_count"] == 2
    assert payload["team"]["substitutes_filled"] == 0


async def _load_and_serialize(db, *, limit: int) -> tuple[int, list[dict]]:
    calls = {"n": 0}
    real_execute = db.execute

    async def counted_execute(statement, *args, **kwargs):
        calls["n"] += 1
        return await real_execute(statement, *args, **kwargs)

    db.execute = counted_execute  # type: ignore[method-assign]
    q = (
        select(Registration)
        .options(*ADMIN_REGISTRATION_LIST_LOAD)
        .order_by(Registration.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    registrations = list(result.scalars().all())
    items = [serialize_admin_registration(r) for r in registrations]
    db.execute = real_execute  # type: ignore[method-assign]
    return calls["n"], items


@requires_db
@pytest.mark.asyncio
async def test_admin_registrations_list_bounded_queries(db):
    event_team = await _make_event(db, name=f"Q Team {uuid.uuid4().hex[:6]}", team=True)
    event_solo = await _make_event(db, name=f"Q Solo {uuid.uuid4().hex[:6]}")

    p = await _make_user_profile(db, email=f"q-t0-{uuid.uuid4().hex}@test.local")
    await _make_team_registration(db, event=event_team, leader=p)
    p = await _make_user_profile(db, email=f"q-s0-{uuid.uuid4().hex}@test.local")
    await _make_solo_registration(db, event=event_solo, profile=p)
    small_queries, small_items = await _load_and_serialize(db, limit=20)
    assert len(small_items) >= 2

    for _ in range(3):
        leader = await _make_user_profile(db, email=f"q-t-{uuid.uuid4().hex}@test.local")
        await _make_team_registration(db, event=event_team, leader=leader)
    for _ in range(2):
        solo = await _make_user_profile(db, email=f"q-s-{uuid.uuid4().hex}@test.local")
        await _make_solo_registration(db, event=event_solo, profile=solo)

    large_queries, large_items = await _load_and_serialize(db, limit=20)
    assert len(large_items) >= 5
    assert large_queries == small_queries
    assert large_queries <= 8
    assert any(i["registration_type"] == "TEAM" for i in large_items)
    assert any(i["registration_type"] == "SOLO" for i in large_items)
