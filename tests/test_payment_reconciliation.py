"""Payment reconciliation, QR idempotency, sync session safety, cancel/late-pay races."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.payments.apply import apply_payment_success
from app.payments.reconciliation import ReconcileOutcome, reconcile_local_payment_state, trace_payment
from app.payments.sync import sync_payment_from_razorpay
from app.services import qr_service
from app.services.registration_service import cancel_unpaid_registration

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


@pytest.mark.asyncio
async def test_qr_regeneration_reactivates_single_row(db, solo_payment_setup):
    registration = solo_payment_setup["registration"]
    first = await qr_service.generate_for_registration(db, registration.id)
    await qr_service.deactivate_for_registration(db, registration.id)
    second = await qr_service.generate_for_registration(db, registration.id)

    count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == registration.id)
        )
    ).scalar_one()
    assert count == 1
    assert second.id == first.id
    assert second.is_active is True


@pytest.mark.asyncio
async def test_qr_double_generate_idempotent(db, solo_payment_setup):
    registration = solo_payment_setup["registration"]
    await qr_service.generate_for_registration(db, registration.id)
    await qr_service.generate_for_registration(db, registration.id)
    count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == registration.id)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_apply_payment_success_after_inactive_qr(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    await qr_service.generate_for_registration(db, registration.id)
    await qr_service.deactivate_for_registration(db, registration.id)

    result = await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")
    assert result.applied is True
    await db.refresh(registration)
    assert registration.status == RegistrationStatus.CONFIRMED

    qr = (
        await db.execute(select(QRCode).where(QRCode.registration_id == registration.id))
    ).scalar_one()
    assert qr.is_active is True


@pytest.mark.asyncio
async def test_cancel_then_late_payment_does_not_confirm(db, solo_payment_setup):
    setup = solo_payment_setup
    profile = setup["profile"]
    payment = setup["payment"]
    registration = setup["registration"]

    await cancel_unpaid_registration(db, registration.id, profile)
    await db.refresh(payment)
    assert payment.status == PaymentStatus.FAILED

    result = await apply_payment_success(db, payment.id, f"pay_late_{uuid.uuid4().hex}")
    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CANCELLED
    assert registration.payment_id is None


@pytest.mark.asyncio
async def test_reconcile_orphan_unlinked_payment(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    registration.payment_id = None
    await db.flush()

    outcome = await reconcile_local_payment_state(
        db, payment, razorpay_payment_id=f"pay_{uuid.uuid4().hex}"
    )
    assert outcome.outcome == ReconcileOutcome.REGISTRATION_UNLINKED


@pytest.mark.asyncio
async def test_reconcile_restores_paid_state(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    razorpay_id = f"pay_rec_{uuid.uuid4().hex}"

    outcome = await reconcile_local_payment_state(db, payment, razorpay_payment_id=razorpay_id)
    assert outcome.outcome == ReconcileOutcome.APPLIED
    await db.refresh(payment)
    assert payment.status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_sync_failure_leaves_session_usable(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    profile = setup["profile"]

    with patch(
        "app.payments.sync.apply_payment_success",
        new=AsyncMock(side_effect=RuntimeError("simulated db failure")),
    ):
        await sync_payment_from_razorpay(db, payment)

    loaded = (
        await db.execute(select(Profile).where(Profile.id == profile.id))
    ).scalar_one_or_none()
    assert loaded is not None
    assert loaded.id == profile.id


@pytest.mark.asyncio
async def test_duplicate_webhook_single_qr_and_receipt(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    razorpay_id = f"pay_dup_{uuid.uuid4().hex}"

    await apply_payment_success(db, payment.id, razorpay_id)
    await apply_payment_success(db, payment.id, razorpay_id)

    qr_count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == registration.id)
        )
    ).scalar_one()
    receipt_count = (
        await db.execute(select(func.count()).select_from(Receipt).where(Receipt.payment_id == payment.id))
    ).scalar_one()
    assert qr_count == 1
    assert receipt_count == 1


@pytest.mark.asyncio
async def test_trace_payment_chain(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    await apply_payment_success(db, payment.id, f"pay_trace_{uuid.uuid4().hex}")

    trace = await trace_payment(db, payment_id=payment.id)
    assert trace.payment_id == payment.id
    assert trace.registration_id == registration.id
    assert trace.payment_status == PaymentStatus.PAID.value
    assert trace.receipt_exists is True


@pytest.mark.asyncio
async def test_create_order_links_payment_to_registration(db):
    from unittest.mock import MagicMock, patch

    from app.payments.create_order import create_payment_order
    from app.payments.sync import sync_payment_from_razorpay

    profile = await _make_user_profile(db, email=f"link-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Link {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)

    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock(
            return_value={"id": f"order_{uuid.uuid4().hex}"}
        )
        result = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.SOLO_REGISTRATION,
            payer=profile,
            registration_id=registration.id,
            sync_payment=sync_payment_from_razorpay,
        )

    payment = (
        await db.execute(select(Payment).where(Payment.id == uuid.UUID(result["paymentId"])))
    ).scalar_one()
    await db.refresh(registration)
    assert registration.payment_id == payment.id
    assert payment.razorpay_order_id == result["razorpayOrderId"]
