"""Receipt data must come from persisted records — no fabrication."""
import uuid

import pytest

from app.core.errors import AppError, NOT_FOUND, RECEIPT_DATA_INCOMPLETE
from app.models.enums import PaymentStatus, RegistrationStatus
from app.services.receipt_service import ensure_receipt, get_receipt_data_context
from tests.conftest import requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_missing_payment_fails(db):
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, uuid.uuid4())
    assert exc.value.code == NOT_FOUND


@pytest.mark.asyncio
async def test_unpaid_payment_fails(db, solo_payment_setup):
    setup = solo_payment_setup
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, setup["payment"].id)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_registration_fails(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.payment_id = None
    await db.flush()
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, payment.id)
    assert exc.value.code == RECEIPT_DATA_INCOMPLETE


@pytest.mark.asyncio
async def test_unconfirmed_registration_fails(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    payment.status = PaymentStatus.PAID
    setup["registration"].status = RegistrationStatus.PENDING
    await db.flush()
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, payment.id)
    assert exc.value.code == RECEIPT_DATA_INCOMPLETE


@pytest.mark.asyncio
async def test_missing_qr_fails(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await db.flush()
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, payment.id)
    assert exc.value.code == RECEIPT_DATA_INCOMPLETE


@pytest.mark.asyncio
async def test_missing_receipt_metadata_fails(db, solo_payment_setup):
    from app.services import qr_service

    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await qr_service.generate_for_registration(db, registration.id)
    await db.flush()
    with pytest.raises(AppError) as exc:
        await get_receipt_data_context(db, payment.id)
    assert exc.value.code == RECEIPT_DATA_INCOMPLETE


@pytest.mark.asyncio
async def test_complete_data_succeeds(db, solo_payment_setup):
    from app.services import qr_service

    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await qr_service.generate_for_registration(db, registration.id)
    await ensure_receipt(db, payment.id)
    await db.flush()

    ctx = await get_receipt_data_context(db, payment.id)
    assert ctx["event_name"] == setup["event"].name
    assert ctx["qr_token"]
    assert ctx["receipt_number"].startswith("KR-")
