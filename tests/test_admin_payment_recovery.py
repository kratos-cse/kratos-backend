"""SUPER ADMIN recover captured payment — authorization and recovery rules."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps_admin import get_current_active_admin, require_super_admin
from app.main import app
from app.models.admin import ADMIN_ROLE_NAME, SUPER_ADMIN_ROLE_NAME, AdminUser
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.payments.admin_recover import (
    PaymentRecoveryError,
    RazorpayCaptureInfo,
    recover_captured_payment,
)
from app.payments.apply import apply_payment_success
from tests.test_event_scoped_rbac import _admin_with_role

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db

CAPTURE = RazorpayCaptureInfo(
    razorpay_payment_id="pay_recover_test",
    amount_paise=25000,
    status="captured",
)


def _super_admin(*, user_id: uuid.UUID | None = None) -> AdminUser:
    admin = _admin_with_role(SUPER_ADMIN_ROLE_NAME, [])
    if user_id is not None:
        admin.user_id = user_id
    return admin


@pytest.fixture
def api_client():
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _noop_audit_log():
    with patch("app.payments.admin_recover.audit_service.log_activity", new=AsyncMock(return_value=None)):
        yield


@pytest.mark.asyncio
async def test_super_admin_recovers_captured_solo_payment(db):
    profile = await _make_user_profile(db, email=f"recover-ok-{uuid.uuid4().hex}@test.local")
    event = await _make_event(db, name=f"Recover {uuid.uuid4().hex[:6]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = await _make_payment(
        db,
        payer=profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
    )
    payment.amount_paise = 25000

    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ):
        result = await recover_captured_payment(
            db,
            _super_admin(),
            registration_id=registration.id,
            payment_id=payment.id,
        )

    assert result.message == "Payment recovered successfully"
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED
    assert result.qr_active is True
    assert result.receipt_generated is True


@pytest.mark.asyncio
async def test_recovery_idempotent_second_run(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.amount_paise = CAPTURE.amount_paise
    admin = _super_admin()

    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ):
        first = await recover_captured_payment(
            db, admin, registration_id=registration.id, payment_id=payment.id
        )
        second = await recover_captured_payment(
            db, admin, registration_id=registration.id, payment_id=payment.id
        )

    assert first.applied is True
    assert second.applied is False
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
async def test_recovery_rejects_not_captured(db, solo_payment_setup):
    setup = solo_payment_setup
    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(PaymentRecoveryError, match="not captured"):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=setup["payment"].id,
            )


@pytest.mark.asyncio
async def test_recovery_rejects_amount_mismatch(db, solo_payment_setup):
    setup = solo_payment_setup
    bad = RazorpayCaptureInfo(razorpay_payment_id="pay_x", amount_paise=1, status="captured")
    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=bad),
    ):
        with pytest.raises(PaymentRecoveryError, match="amount"):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=setup["payment"].id,
            )


@pytest.mark.asyncio
async def test_recovery_rejects_wrong_registration_link(db, solo_payment_setup):
    setup = solo_payment_setup
    other_profile = await _make_user_profile(db, email=f"other-{uuid.uuid4().hex}@t.l")
    other_reg = await _make_solo_registration(db, event=setup["event"], profile=other_profile)
    other_payment = await _make_payment(
        db,
        payer=other_profile,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=other_reg,
    )
    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ):
        with pytest.raises(PaymentRecoveryError, match="does not belong"):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=other_payment.id,
            )


@pytest.mark.asyncio
async def test_recovery_rejects_cancelled_registration(db, solo_payment_setup):
    from app.services.registration_service import cancel_unpaid_registration

    setup = solo_payment_setup
    await cancel_unpaid_registration(db, setup["registration"].id, setup["profile"])
    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ):
        with pytest.raises(PaymentRecoveryError, match="cancelled registration"):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=setup["payment"].id,
            )


@pytest.mark.asyncio
async def test_team_recovery_confirms_team(db, team_payment_setup):
    setup = team_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.amount_paise = CAPTURE.amount_paise

    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ):
        result = await recover_captured_payment(
            db,
            _super_admin(),
            registration_id=registration.id,
            payment_id=payment.id,
        )

    await db.refresh(registration)
    assert result.registration_status == RegistrationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_failed_apply_does_not_mark_paid(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    with patch(
        "app.payments.admin_recover.fetch_razorpay_capture_for_order",
        new=AsyncMock(return_value=CAPTURE),
    ), patch(
        "app.payments.admin_recover.apply_payment_success",
        new=AsyncMock(side_effect=RuntimeError("simulated failure")),
    ):
        with pytest.raises(PaymentRecoveryError):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=payment.id,
            )
    await db.refresh(payment)
    assert payment.status == PaymentStatus.CREATED


def test_http_super_admin_reaches_handler(api_client):
    app.dependency_overrides[require_super_admin] = lambda: _super_admin()
    reg_id = uuid.uuid4()
    pay_id = uuid.uuid4()
    with patch(
        "app.api.v1.endpoints.admin_ops.recover_captured_payment",
        new=AsyncMock(side_effect=PaymentRecoveryError("Registration not found.", status_code=404)),
    ):
        resp = api_client.post(
            f"/api/v1/admin/registrations/{reg_id}/recover-captured-payment",
            json={"payment_id": str(pay_id)},
            headers={"Authorization": "Bearer test"},
        )
    assert resp.status_code == 404


def test_http_admin_role_receives_403(api_client):
    app.dependency_overrides[get_current_active_admin] = lambda: _admin_with_role(
        ADMIN_ROLE_NAME, ["registration-read", "payment-read"]
    )

    resp = api_client.post(
        f"/api/v1/admin/registrations/{uuid.uuid4()}/recover-captured-payment",
        json={"payment_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403


def test_http_coordinator_staff_receives_403(api_client):
    from app.models.admin import EVENT_COORDINATOR_ROLE_NAME

    app.dependency_overrides[get_current_active_admin] = lambda: _admin_with_role(
        EVENT_COORDINATOR_ROLE_NAME, ["registration-read"]
    )

    resp = api_client.post(
        f"/api/v1/admin/registrations/{uuid.uuid4()}/recover-captured-payment",
        json={"payment_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403


def test_http_participant_without_admin_receives_403(api_client):
    async def _deny():
        raise HTTPException(status_code=403, detail="You do not have active administrative privileges.")

    app.dependency_overrides[get_current_active_admin] = _deny
    resp = api_client.post(
        f"/api/v1/admin/registrations/{uuid.uuid4()}/recover-captured-payment",
        json={"payment_id": str(uuid.uuid4())},
        headers={"Authorization": "Bearer test"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_recovery_does_not_create_razorpay_order(db, solo_payment_setup):
    setup = solo_payment_setup
    with patch("app.payments.create_order.get_razorpay") as mock_rp:
        mock_rp.return_value.order.create = AsyncMock()
        with patch(
            "app.payments.admin_recover.fetch_razorpay_capture_for_order",
            new=AsyncMock(return_value=CAPTURE),
        ):
            await recover_captured_payment(
                db,
                _super_admin(),
                registration_id=setup["registration"].id,
                payment_id=setup["payment"].id,
            )
        mock_rp.return_value.order.create.assert_not_called()
