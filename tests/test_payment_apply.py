import uuid
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import patch

from app.models.enums import PaymentStatus, PaymentType
from app.services.payment_apply import apply_payment_failure, apply_payment_success


@dataclass
class FakePayment:
    id: uuid.UUID
    payment_type: PaymentType
    team_member_id: Optional[uuid.UUID]
    payer_profile_id: uuid.UUID
    amount_paise: int


class StubStore:
    """~25-line in-memory stand-in for SqlAlchemyPaymentsStore, so the state
    machine is exercised with no real database session."""

    def __init__(self, initial_status: PaymentStatus, payment_type: PaymentType, **payment_kwargs):
        self.status = initial_status
        self.payment = FakePayment(
            id=payment_kwargs.get("id", uuid.uuid4()),
            payment_type=payment_type,
            team_member_id=payment_kwargs.get("team_member_id"),
            payer_profile_id=payment_kwargs.get("payer_profile_id", uuid.uuid4()),
            amount_paise=payment_kwargs.get("amount_paise", 50000),
        )
        self.confirm_calls = {"solo": 0, "team": 0, "topup": 0}
        self.committed = False
        self.rolled_back = False

    async def transition_payment_status(self, payment_id, from_statuses, patch):
        if self.status not in from_statuses:
            return None
        self.status = patch["status"]
        return self.payment

    async def confirm_solo_registration(self, payment_id):
        self.confirm_calls["solo"] += 1

    async def confirm_team_registration(self, payment_id):
        self.confirm_calls["team"] += 1

    async def confirm_team_member_topup(self, team_member_id):
        self.confirm_calls["topup"] += 1

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class TestApplyPaymentSuccessIdempotency:
    async def test_applies_and_runs_side_effects_once_on_first_call(self):
        store = StubStore(PaymentStatus.CREATED, PaymentType.SOLO_REGISTRATION)
        with patch("app.services.handoffs.send_notification") as send_notification:
            result = await apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is True
        assert store.status == PaymentStatus.PAID
        assert store.confirm_calls["solo"] == 1
        assert store.committed is True
        send_notification.assert_called_once()

    async def test_duplicate_call_is_a_noop(self):
        store = StubStore(PaymentStatus.CREATED, PaymentType.SOLO_REGISTRATION)
        with patch("app.services.handoffs.send_notification") as send_notification, patch(
            "app.services.handoffs.trigger_qr"
        ) as trigger_qr:
            first = await apply_payment_success(store, store.payment.id, "razorpay_pay_1")
            second = await apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert first.applied is True
        assert second.applied is False
        assert second.payment is None
        assert store.confirm_calls["solo"] == 1  # not 2
        send_notification.assert_called_once()  # not twice
        trigger_qr.assert_called_once()  # not twice

    async def test_does_not_resurrect_a_refunded_payment(self):
        store = StubStore(PaymentStatus.REFUNDED, PaymentType.SOLO_REGISTRATION)
        with patch("app.services.handoffs.send_notification") as send_notification:
            result = await apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is False
        assert store.status == PaymentStatus.REFUNDED
        assert store.confirm_calls["solo"] == 0
        assert store.rolled_back is True
        send_notification.assert_not_called()

    async def test_allows_success_after_a_prior_failed_attempt(self):
        store = StubStore(PaymentStatus.FAILED, PaymentType.SOLO_REGISTRATION)
        with patch("app.services.handoffs.send_notification"):
            result = await apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is True
        assert store.status == PaymentStatus.PAID

    async def test_routes_team_member_topup_through_confirm_team_member_topup(self):
        store = StubStore(PaymentStatus.CREATED, PaymentType.TEAM_MEMBER_TOPUP, team_member_id=uuid.uuid4())
        with patch("app.services.handoffs.send_notification"):
            result = await apply_payment_success(store, store.payment.id, "razorpay_pay_2")

        assert result.applied is True
        assert store.confirm_calls["topup"] == 1
        assert store.confirm_calls["solo"] == 0


class TestApplyPaymentFailureIdempotency:
    async def test_transitions_created_to_failed(self):
        store = StubStore(PaymentStatus.CREATED, PaymentType.SOLO_REGISTRATION)
        result = await apply_payment_failure(store, store.payment.id)
        assert result.applied is True
        assert store.status == PaymentStatus.FAILED
        assert store.committed is True

    async def test_never_moves_paid_to_failed(self):
        store = StubStore(PaymentStatus.PAID, PaymentType.SOLO_REGISTRATION)
        result = await apply_payment_failure(store, store.payment.id)
        assert result.applied is False
        assert store.status == PaymentStatus.PAID
        assert store.rolled_back is True

    async def test_never_moves_refunded_to_failed(self):
        store = StubStore(PaymentStatus.REFUNDED, PaymentType.SOLO_REGISTRATION)
        result = await apply_payment_failure(store, store.payment.id)
        assert result.applied is False
        assert store.status == PaymentStatus.REFUNDED
