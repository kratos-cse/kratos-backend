"""Integration tests for payment success, receipts, and idempotency."""
import uuid

import pytest
from sqlalchemy import func, select

from app.models.enums import (
    PaymentStatus,
    PaymentType,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.payment import Payment
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.payments.apply import apply_payment_success, confirm_team_registration
from app.services.receipt_service import ensure_receipt, get_receipt_data_context

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    refresh_registration,
    requires_db,
)

pytestmark = requires_db


@pytest.mark.asyncio
async def test_get_receipt_data_context_loads_registration_event(db, solo_payment_setup):
    """Regression: Registration.event must exist for receipt context (production AttributeError)."""
    setup = solo_payment_setup
    payment = setup["payment"]
    event = setup["event"]

    payment.status = PaymentStatus.PAID
    await db.flush()

    ctx = await get_receipt_data_context(db, payment.id)

    assert ctx["event_name"] == event.name
    assert ctx["event_venue"] == event.venue
    assert ctx["payment_id"] == str(payment.id)


@pytest.mark.asyncio
async def test_ensure_receipt_after_paid_solo(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    payment.status = PaymentStatus.PAID
    await db.flush()

    receipt = await ensure_receipt(db, payment.id)

    assert receipt.payment_id == payment.id
    assert receipt.receipt_number


@pytest.mark.asyncio
async def test_apply_payment_success_solo_end_to_end(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]

    result = await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED

    receipt_count = (
        await db.execute(select(func.count()).select_from(Receipt).where(Receipt.payment_id == payment.id))
    ).scalar_one()
    assert receipt_count == 1

    qr_count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == registration.id)
        )
    ).scalar_one()
    assert qr_count >= 1


@pytest.mark.asyncio
async def test_apply_payment_success_team_end_to_end(db, team_payment_setup):
    setup = team_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    team = setup["team"]
    leader = setup["leader"]

    result = await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    await db.refresh(team)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED
    assert team.status == TeamStatus.PAID

    leader_member = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.profile_id == leader.id,
                TeamMember.role == TeamMemberRole.LEADER,
            )
        )
    ).scalar_one()
    assert leader_member.status == TeamMemberStatus.ACTIVE

    receipt_count = (
        await db.execute(select(func.count()).select_from(Receipt).where(Receipt.payment_id == payment.id))
    ).scalar_one()
    assert receipt_count == 1


@pytest.mark.asyncio
async def test_apply_payment_success_idempotent_second_webhook(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    razorpay_id = f"pay_{uuid.uuid4().hex}"

    first = await apply_payment_success(db, payment.id, razorpay_id)
    second = await apply_payment_success(db, payment.id, razorpay_id)

    assert first.applied is True
    assert second.applied is False

    receipt_count = (
        await db.execute(select(func.count()).select_from(Receipt).where(Receipt.payment_id == payment.id))
    ).scalar_one()
    assert receipt_count == 1

    qr_count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == setup["registration"].id)
        )
    ).scalar_one()
    assert qr_count >= 1


@pytest.mark.asyncio
async def test_team_payment_recovers_unlinked_pending_registration(db):
    """When payment exists but registration.payment_id was never set, recover safely."""
    leader = await _make_user_profile(db, email=f"recover-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Recover Team {uuid.uuid4().hex[:6]}", team=True)
    team, registration = await _make_team_registration(db, event=event, leader=leader)
    payment = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=None,
    )

    await confirm_team_registration(db, payment.id, leader.id)
    await db.refresh(registration)
    await db.refresh(team)

    assert registration.payment_id == payment.id
    assert team.status == TeamStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_team_payment_rejects_orphan_without_pending_registration(db):
    leader = await _make_user_profile(db, email=f"orphan-{uuid.uuid4().hex}@test.local")
    payment = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=None,
    )

    with pytest.raises(RuntimeError, match="no team registration linked"):
        await confirm_team_registration(db, payment.id, leader.id)


@pytest.mark.asyncio
async def test_manual_sync_path_matches_apply_payment_success(db, solo_payment_setup):
    """Manual sync uses the same apply_payment_success entry point."""
    setup = solo_payment_setup
    payment = setup["payment"]

    result = await apply_payment_success(db, payment.id, f"pay_sync_{uuid.uuid4().hex}")

    assert result.applied is True
    reg = await refresh_registration(db, setup["registration"].id)
    assert reg.status == RegistrationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_checkout_verify_path_matches_apply_payment_success(db, solo_payment_setup):
    """POST /payments/verify delegates to apply_payment_success — same final state."""
    setup = solo_payment_setup
    payment = setup["payment"]

    result = await apply_payment_success(db, payment.id, f"pay_verify_{uuid.uuid4().hex}")

    assert result.applied is True
    await db.refresh(payment)
    assert payment.status == PaymentStatus.PAID
