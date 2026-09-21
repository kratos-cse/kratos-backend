import uuid
from dataclasses import dataclass
from typing import Optional
from unittest.mock import patch

import pytest

from app.models.enums import PaymentStatus, PaymentType
from app.services.payment_apply import ApplyResult, PaymentsStore, apply_payment_failure, apply_payment_success


@dataclass
class FakePayment:
    id: uuid.UUID
    payment_type: str
    team_member_id: Optional[uuid.UUID]
    payer_profile_id: uuid.UUID
    amount_paise: int


class StubStore:
    """In-memory stand-in for PaymentsStore so the state machine is exercised
    with no database session."""

    def __init__(self, initial_status: str, payment_type: str, **kwargs):
        self.status = initial_status
        self.payment = FakePayment(
            id=kwargs.get("id", uuid.uuid4()),
            payment_type=payment_type,
            team_member_id=kwargs.get("team_member_id"),
            payer_profile_id=kwargs.get("payer_profile_id", uuid.uuid4()),
            amount_paise=kwargs.get("amount_paise", 50000),
        )
        self.confirm_calls = {"solo": 0, "team": 0, "topup": 0}
        self.committed = False
        self.rolled_back = False

    def transition_payment_status(self, payment_id: uuid.UUID, from_statuses: list[str], patch_data: dict) -> Optional[FakePayment]:
        if self.status not in from_statuses:
            return None
        self.status = patch_data["status"]
        return self.payment

    def confirm_solo_registration(self, payment_id: uuid.UUID) -> None:
        self.confirm_calls["solo"] += 1

    def confirm_team_registration(self, payment_id: uuid.UUID) -> None:
        self.confirm_calls["team"] += 1

    def confirm_team_member_topup(self, team_member_id: uuid.UUID) -> None:
        self.confirm_calls["topup"] += 1

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class TestApplyPaymentSuccessIdempotency:
    def test_applies_and_runs_side_effects_once_on_first_call(self):
        store = StubStore(PaymentStatus.CREATED.value, PaymentType.SOLO_REGISTRATION.value)
        with patch("app.services.handoffs.send_notification") as send_notif, patch(
            "app.services.handoffs.trigger_qr"
        ) as trigger_qr, patch("app.services.handoffs.issue_receipt") as issue_receipt:
            result = apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is True
        assert store.status == PaymentStatus.PAID.value
        assert store.confirm_calls["solo"] == 1
        assert store.committed is True
        send_notif.assert_called_once()
        trigger_qr.assert_called_once()
        issue_receipt.assert_called_once()

    def test_duplicate_call_is_a_noop(self):
        store = StubStore(PaymentStatus.CREATED.value, PaymentType.SOLO_REGISTRATION.value)
        with patch("app.services.handoffs.send_notification") as send_notif, patch(
            "app.services.handoffs.trigger_qr"
        ) as trigger_qr, patch("app.services.handoffs.issue_receipt"):
            first = apply_payment_success(store, store.payment.id, "razorpay_pay_1")
            second = apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert first.applied is True
        assert second.applied is False
        assert second.payment is None
        assert store.confirm_calls["solo"] == 1  # not incremented twice
        send_notif.assert_called_once()
        trigger_qr.assert_called_once()

    def test_does_not_resurrect_a_refunded_payment(self):
        store = StubStore(PaymentStatus.REFUNDED.value, PaymentType.SOLO_REGISTRATION.value)
        with patch("app.services.handoffs.send_notification") as send_notif:
            result = apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is False
        assert store.status == PaymentStatus.REFUNDED.value
        assert store.confirm_calls["solo"] == 0
        assert store.rolled_back is True
        send_notif.assert_not_called()

    def test_allows_success_after_a_prior_failed_attempt(self):
        store = StubStore(PaymentStatus.FAILED.value, PaymentType.SOLO_REGISTRATION.value)
        with patch("app.services.handoffs.send_notification"):
            result = apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is True
        assert store.status == PaymentStatus.PAID.value

    def test_routes_team_registration_through_confirm_team_registration(self):
        store = StubStore(PaymentStatus.CREATED.value, PaymentType.TEAM_REGISTRATION.value)
        with patch("app.services.handoffs.send_notification"):
            result = apply_payment_success(store, store.payment.id, "razorpay_pay_1")

        assert result.applied is True
        assert store.confirm_calls["team"] == 1
        assert store.confirm_calls["solo"] == 0

    def test_routes_team_member_topup_through_confirm_team_member_topup(self):
        store = StubStore(
            PaymentStatus.CREATED.value,
            PaymentType.TEAM_MEMBER_TOPUP.value,
            team_member_id=uuid.uuid4(),
        )
        with patch("app.services.handoffs.send_notification"):
            result = apply_payment_success(store, store.payment.id, "razorpay_pay_2")

        assert result.applied is True
        assert store.confirm_calls["topup"] == 1
        assert store.confirm_calls["solo"] == 0


class TestApplyPaymentFailureIdempotency:
    def test_transitions_created_to_failed(self):
        store = StubStore(PaymentStatus.CREATED.value, PaymentType.SOLO_REGISTRATION.value)
        result = apply_payment_failure(store, store.payment.id)
        assert result.applied is True
        assert store.status == PaymentStatus.FAILED.value
        assert store.committed is True

    def test_never_moves_paid_to_failed(self):
        store = StubStore(PaymentStatus.PAID.value, PaymentType.SOLO_REGISTRATION.value)
        result = apply_payment_failure(store, store.payment.id)
        assert result.applied is False
        assert store.status == PaymentStatus.PAID.value
        assert store.rolled_back is True

    def test_never_moves_refunded_to_failed(self):
        store = StubStore(PaymentStatus.REFUNDED.value, PaymentType.SOLO_REGISTRATION.value)
        result = apply_payment_failure(store, store.payment.id)
        assert result.applied is False
        assert store.status == PaymentStatus.REFUNDED.value
