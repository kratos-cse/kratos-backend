import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.enums import PaymentStatus, PaymentType
from app.services.refund import RefundError, refund_payment


class FakePayment:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.status = kwargs.get("status", PaymentStatus.PAID.value)
        self.payment_type = kwargs.get("payment_type", PaymentType.SOLO_REGISTRATION.value)
        self.payer_profile_id = kwargs.get("payer_profile_id", uuid.uuid4())
        self.team_member_id = kwargs.get("team_member_id")
        self.razorpay_payment_id = kwargs.get("razorpay_payment_id", "pay_test123")
        self.amount_paise = kwargs.get("amount_paise", 50000)
        self.refund_id = None
        self.refund_amount_paise = None
        self.refunded_at = None
        self.refund_reason = None


class TestRefundPayment:
    def test_payment_not_found_raises_404(self):
        db = MagicMock()
        exec_mock = MagicMock()
        exec_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = exec_mock

        with pytest.raises(RefundError) as exc_info:
            refund_payment(db, uuid.uuid4(), "Test reason")
        assert exc_info.value.status == 404

    def test_payment_not_paid_raises_409(self):
        db = MagicMock()
        payment = FakePayment(status=PaymentStatus.CREATED.value)
        exec_mock = MagicMock()
        exec_mock.scalar_one_or_none.return_value = payment
        db.execute.return_value = exec_mock

        with pytest.raises(RefundError) as exc_info:
            refund_payment(db, payment.id, "Test reason")
        assert exc_info.value.status == 409
        assert "not PAID" in str(exc_info.value)

    def test_payment_without_razorpay_id_raises_409(self):
        db = MagicMock()
        payment = FakePayment(status=PaymentStatus.PAID.value, razorpay_payment_id=None)
        exec_mock = MagicMock()
        exec_mock.scalar_one_or_none.return_value = payment
        db.execute.return_value = exec_mock

        with pytest.raises(RefundError) as exc_info:
            refund_payment(db, payment.id, "Test reason")
        assert exc_info.value.status == 409
        assert "has no razorpay_payment_id" in str(exc_info.value)

    def test_successful_refund(self):
        db = MagicMock()
        payment = FakePayment(status=PaymentStatus.PAID.value, razorpay_payment_id="pay_123", amount_paise=50000)

        # 1st execute: select Payment
        # 2nd execute: update Payment returning
        # 3rd execute: update Registration
        select_mock = MagicMock()
        select_mock.scalar_one_or_none.return_value = payment
        update_mock = MagicMock()
        update_mock.scalar_one_or_none.return_value = payment

        db.execute.side_effect = [select_mock, update_mock, MagicMock()]

        fake_client = MagicMock()
        fake_client.payment.refund.return_value = {"id": "rfnd_test123", "amount": 50000}

        with patch("app.services.refund.get_razorpay", return_value=fake_client), patch(
            "app.services.refund.send_notification"
        ) as send_notif:
            result = refund_payment(db, payment.id, "User requested refund")

        assert result.payment_id == payment.id
        assert result.refund_id == "rfnd_test123"
        assert result.refund_amount_paise == 50000
        db.commit.assert_called_once()
        send_notif.assert_called_once()
