"""Payment create-order retry, idempotency, and terminal-state protection."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from app.core.errors import PAYMENT_ALREADY_PROCESSED, AppError
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.payments.create_order import (
    create_payment_order,
    existing_payment_order_response,
    order_response,
    resolve_registration_for_create_order,
)

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


async def _noop_sync(_db, payment):
    return payment


@pytest.mark.asyncio
async def test_first_create_order_creates_payment(db):
    profile = await _make_user_profile(db, email=f"first-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"First Pay {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)

    mock_order = {"id": f"order_{uuid.uuid4().hex}"}
    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock(return_value=mock_order)
        result = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.SOLO_REGISTRATION,
            payer=profile,
            registration_id=registration.id,
            sync_payment=_noop_sync,
        )

    assert result["razorpayOrderId"] == mock_order["id"]
    payment = (
        await db.execute(select(Payment).where(Payment.id == uuid.UUID(result["paymentId"])))
    ).scalar_one()
    assert payment.status == PaymentStatus.CREATED
    assert registration.payment_id == payment.id


@pytest.mark.asyncio
async def test_duplicate_active_created_payment_is_idempotent(db):
    profile = await _make_user_profile(db, email=f"dup-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"Dup Pay {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    existing = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
    )

    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock()
        result = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.SOLO_REGISTRATION,
            payer=profile,
            registration_id=registration.id,
            sync_payment=_noop_sync,
        )
        mock_rp.return_value.order.create.assert_not_called()

    assert result["paymentId"] == str(existing.id)
    assert result["razorpayOrderId"] == existing.razorpay_order_id
    count = await db.scalar(select(func.count()).select_from(Payment))
    assert count == 1


@pytest.mark.asyncio
async def test_failed_payment_allows_new_order_and_preserves_history(db):
    profile = await _make_user_profile(db, email=f"retry-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"Retry Pay {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    failed = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.FAILED,
    )
    failed_id = failed.id

    mock_order = {"id": f"order_{uuid.uuid4().hex}"}
    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock(return_value=mock_order)
        result = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.SOLO_REGISTRATION,
            payer=profile,
            registration_id=registration.id,
            sync_payment=_noop_sync,
        )

    new_payment_id = uuid.UUID(result["paymentId"])
    assert new_payment_id != failed_id
    assert registration.payment_id == new_payment_id

    old_payment = (await db.execute(select(Payment).where(Payment.id == failed_id))).scalar_one()
    assert old_payment.status == PaymentStatus.FAILED
    assert old_payment.razorpay_order_id == failed.razorpay_order_id


@pytest.mark.asyncio
async def test_paid_registration_rejects_new_order(db):
    profile = await _make_user_profile(db, email=f"paid-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"Paid Pay {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    registration.status = RegistrationStatus.CONFIRMED
    await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )
    await db.flush()

    with pytest.raises(AppError) as exc:
        await resolve_registration_for_create_order(
            db,
            event_id=event.id,
            payer=profile,
            payment_type=PaymentType.SOLO_REGISTRATION,
            registration_id=registration.id,
        )
    assert exc.value.code == PAYMENT_ALREADY_PROCESSED


@pytest.mark.asyncio
async def test_paid_payment_rejects_new_order(db):
    profile = await _make_user_profile(db, email=f"paidp-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"PaidP {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
        status=PaymentStatus.PAID,
    )

    with pytest.raises(AppError) as exc:
        await existing_payment_order_response(db, registration, sync_payment=_noop_sync)
    assert exc.value.code == PAYMENT_ALREADY_PROCESSED


@pytest.mark.asyncio
async def test_cancelled_checkout_retry_reuses_created_order(db):
    """Simulates Pay -> Razorpay cancel -> Pay Again while payment stays CREATED."""
    leader = await _make_user_profile(db, email=f"cancel-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"Cancel {uuid.uuid4().hex[:6]}", team=True)
    _team, registration = await _make_team_registration(db, event=event, leader=leader)
    first = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
    )

    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock()
        second = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.TEAM_REGISTRATION,
            payer=leader,
            registration_id=registration.id,
            sync_payment=_noop_sync,
        )
        mock_rp.return_value.order.create.assert_not_called()

    assert second["paymentId"] == str(first.id)
    assert second["razorpayOrderId"] == first.razorpay_order_id


@pytest.mark.asyncio
async def test_team_create_order_without_registration_id_finds_linked_payment(db):
    leader = await _make_user_profile(db, email=f"team-{uuid.uuid4().hex}@pay.test")
    event = await _make_event(db, name=f"Team Retry {uuid.uuid4().hex[:6]}", team=True)
    _team, registration = await _make_team_registration(db, event=event, leader=leader)
    existing = await _make_payment(
        db,
        payer=leader,
        payment_type=PaymentType.TEAM_REGISTRATION,
        registration=registration,
    )

    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = MagicMock()
        result = await create_payment_order(
            db,
            event_id=event.id,
            payment_type=PaymentType.TEAM_REGISTRATION,
            payer=leader,
            registration_id=None,
            sync_payment=_noop_sync,
        )

    assert result["paymentId"] == str(existing.id)
