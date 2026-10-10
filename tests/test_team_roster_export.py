"""Per-event team roster export — confirmed + paid teams only."""
import uuid

import pytest
from sqlalchemy import select

from app.models.enums import (
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.event import EventRegistrationRule
from app.models.team import TeamMember
from app.services import admin_ops_service as ops

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


@pytest.mark.asyncio
async def test_roster_export_excludes_unpaid_forming_team(db):
    leader = await _make_user_profile(db, email=f"exp-lead-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Export Ev {uuid.uuid4().hex[:6]}", team=True)
    team, _reg = await _make_team_registration(db, event=event, leader=leader)
    team.status = TeamStatus.FORMING
    await db.flush()

    _, rows = await ops.build_team_roster_export_rows(db, event.id)
    assert rows == []


@pytest.mark.asyncio
async def test_roster_export_includes_confirmed_paid_team_with_members(db):
    leader = await _make_user_profile(db, email=f"exp-paid-{uuid.uuid4().hex}@t.local")
    event = await _make_event(db, name=f"Paid Export {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )
    registration.status = RegistrationStatus.CONFIRMED
    team.status = TeamStatus.PAID
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

    _, rows = await ops.build_team_roster_export_rows(db, event.id)
    assert len(rows) >= 1
    assert any(r[1] == team.name for r in rows)
    assert any("Leader" in str(r[4]) for r in rows)
