"""Receipt PDF authorization — no async lazy-load (MissingGreenlet) on Registration.team."""
import uuid

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select

from app.api.deps_receipt import assert_can_access_receipt, authorize_payment_receipt_access
from app.core.receipt_token import create_receipt_access_token
from app.core.security import create_access_token
from app.models.enums import (
    PaymentStatus,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.team import TeamMember
from app.services import qr_service
from app.services.receipt_service import ensure_receipt, get_receipt_data_context, render_pdf_receipt

from tests.conftest import _make_user_profile, requires_db


async def _prepare_solo_paid(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await qr_service.generate_for_registration(db, registration.id)
    await ensure_receipt(db, payment.id)
    await db.flush()
    return setup


async def _prepare_team_paid(db, team_payment_setup):
    setup = team_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    team = setup["team"]
    leader = setup["leader"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    team.status = TeamStatus.PAID
    leader_member = await db.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id,
            TeamMember.profile_id == leader.id,
        )
    )
    assert leader_member is not None
    leader_member.status = TeamMemberStatus.ACTIVE
    await qr_service.generate_for_team_member(db, leader_member.id)
    await ensure_receipt(db, payment.id)
    await db.flush()
    return setup


@pytest.mark.asyncio
@requires_db
async def test_solo_receipt_pdf_auth_and_render(db, solo_payment_setup):
    setup = await _prepare_solo_paid(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]

    await assert_can_access_receipt(db, payment, profile)
    ctx = await get_receipt_data_context(db, payment.id)
    pdf = render_pdf_receipt(ctx)
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
@requires_db
async def test_team_leader_payer_receipt_pdf(db, team_payment_setup):
    setup = await _prepare_team_paid(db, team_payment_setup)
    payment = setup["payment"]
    leader = setup["leader"]

    await assert_can_access_receipt(db, payment, leader)
    pdf = render_pdf_receipt(await get_receipt_data_context(db, payment.id))
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
@requires_db
async def test_team_member_receipt_access_no_missing_greenlet(db, team_payment_setup):
    """Non-payer team member hits assert_can_view_registration → Registration.team must be preloaded."""
    setup = await _prepare_team_paid(db, team_payment_setup)
    payment = setup["payment"]
    team = setup["team"]

    member = await _make_user_profile(db, email=f"member-{uuid.uuid4().hex}@t.local")
    db.add(
        TeamMember(
            team_id=team.id,
            event_id=team.event_id,
            profile_id=member.id,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    await db.flush()

    await assert_can_access_receipt(db, payment, member)
    pdf = render_pdf_receipt(await get_receipt_data_context(db, payment.id))
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
@requires_db
async def test_unauthorized_user_denied_receipt(db, solo_payment_setup):
    setup = await _prepare_solo_paid(db, solo_payment_setup)
    payment = setup["payment"]
    stranger = await _make_user_profile(db, email=f"stranger-{uuid.uuid4().hex}@t.local")

    with pytest.raises(HTTPException) as exc:
        await assert_can_access_receipt(db, payment, stranger)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@requires_db
async def test_receipt_token_authorizes_pdf_path(db, solo_payment_setup):
    setup = await _prepare_solo_paid(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]
    token, _ = create_receipt_access_token(payment.id, profile.id)

    payer = await authorize_payment_receipt_access(
        payment.id,
        db=db,
        credentials=None,
        receipt_token=token,
    )
    assert payer.id == profile.id
    pdf = render_pdf_receipt(await get_receipt_data_context(db, payment.id))
    assert pdf[:4] == b"%PDF"


@pytest.mark.asyncio
@requires_db
async def test_bearer_authorizes_payment_receipt_pdf_endpoint_flow(db, solo_payment_setup):
    setup = await _prepare_solo_paid(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]
    session_token, _ = create_access_token(profile.user_id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=session_token)

    payer = await authorize_payment_receipt_access(
        payment.id,
        db=db,
        credentials=credentials,
        receipt_token=None,
    )
    assert payer.id == profile.id
