from unittest.mock import patch

import pytest

from app.payments import apply as apply_module
from app.payments.apply import PaymentRow, apply_payment_failure, apply_payment_success


class StubStore:
    """~20-line in-memory stand-in for the real Supabase-backed store, so the
    state machine is exercised with no network/database."""

    def __init__(self, initial_status, payment_type, payment_id="pay_1", team_member_id=None):
        self.status = initial_status
        self.payment_type = payment_type
        self.payment_id = payment_id
        self.team_member_id = team_member_id
        self.confirm_calls = {"solo": 0, "team": 0, "topup": 0}

    def transition_payment_status(self, payment_id, from_statuses, patch):
        if self.status not in from_statuses:
            return None
        self.status = patch["status"]
        return PaymentRow(
            id=self.payment_id,
            payment_type=self.payment_type,
            team_member_id=self.team_member_id,
            payer_profile_id="profile_1",
            amount_paise=50000,
        )

    def confirm_solo_registration(self, payment_id):
        self.confirm_calls["solo"] += 1

    def confirm_team_registration(self, payment_id):
        self.confirm_calls["team"] += 1

    def confirm_team_member_topup(self, team_member_id):
        self.confirm_calls["topup"] += 1


@pytest.fixture(autouse=True)
def mock_handoffs():
    with patch.object(apply_module, "trigger_qr") as trigger_qr, patch.object(
        apply_module, "send_notification"
    ) as send_notification, patch.object(apply_module, "issue_receipt") as issue_receipt:
        yield {"trigger_qr": trigger_qr, "send_notification": send_notification, "issue_receipt": issue_receipt}


class TestApplyPaymentSuccessIdempotency:
    def test_applies_and_runs_side_effects_once_on_first_call(self, mock_handoffs):
        store = StubStore("CREATED", "SOLO_REGISTRATION")
        result = apply_payment_success("pay_1", "razorpay_pay_1", store)

        assert result.applied is True
        assert store.status == "PAID"
        assert store.confirm_calls["solo"] == 1
        mock_handoffs["send_notification"].assert_called_once()

    def test_duplicate_call_is_a_noop(self, mock_handoffs):
        store = StubStore("CREATED", "SOLO_REGISTRATION")

        first = apply_payment_success("pay_1", "razorpay_pay_1", store)
        second = apply_payment_success("pay_1", "razorpay_pay_1", store)

        assert first.applied is True
        assert second.applied is False
        assert second.payment is None
        assert store.confirm_calls["solo"] == 1  # not 2
        mock_handoffs["send_notification"].assert_called_once()  # not twice
        mock_handoffs["trigger_qr"].assert_called_once()  # not twice

    def test_does_not_resurrect_a_refunded_payment(self, mock_handoffs):
        store = StubStore("REFUNDED", "SOLO_REGISTRATION")

        result = apply_payment_success("pay_1", "razorpay_pay_1", store)

        assert result.applied is False
        assert store.status == "REFUNDED"
        assert store.confirm_calls["solo"] == 0
        mock_handoffs["send_notification"].assert_not_called()

    def test_allows_success_after_a_prior_failed_attempt(self, mock_handoffs):
        store = StubStore("FAILED", "SOLO_REGISTRATION")

        result = apply_payment_success("pay_1", "razorpay_pay_1", store)

        assert result.applied is True
        assert store.status == "PAID"

    def test_routes_team_member_topup_through_confirm_team_member_topup(self, mock_handoffs):
        store = StubStore("CREATED", "TEAM_MEMBER_TOPUP", payment_id="pay_2", team_member_id="tm_1")

        result = apply_payment_success("pay_2", "razorpay_pay_2", store)

        assert result.applied is True
        assert store.confirm_calls["topup"] == 1
        assert store.confirm_calls["solo"] == 0


class TestApplyPaymentFailureIdempotency:
    def test_transitions_created_to_failed(self):
        store = StubStore("CREATED", "SOLO_REGISTRATION")
        result = apply_payment_failure("pay_1", store)
        assert result.applied is True
        assert store.status == "FAILED"

    def test_never_moves_paid_to_failed(self):
        store = StubStore("PAID", "SOLO_REGISTRATION")
        result = apply_payment_failure("pay_1", store)
        assert result.applied is False
        assert store.status == "PAID"

    def test_never_moves_refunded_to_failed(self):
        store = StubStore("REFUNDED", "SOLO_REGISTRATION")
        result = apply_payment_failure("pay_1", store)
        assert result.applied is False
        assert store.status == "REFUNDED"
