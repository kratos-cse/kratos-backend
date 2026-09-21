import inspect

import pytest

from app.payments.amounts import compute_amount_paise, compute_amount_paise_from_fee


class TestComputeAmountPaiseFromFee:
    def test_converts_rupee_fee_to_integer_paise(self):
        amount = compute_amount_paise_from_fee(500)
        assert amount == 50000
        assert isinstance(amount, int)

    def test_single_fee_not_multiplied_by_member_count(self):
        amount = compute_amount_paise_from_fee(250)
        assert amount == 25000

    def test_rejects_a_negative_fee(self):
        with pytest.raises(ValueError):
            compute_amount_paise_from_fee(-50)

    def test_async_helper_has_no_client_amount_param(self):
        params = set(inspect.signature(compute_amount_paise).parameters)
        assert "amount" not in params
        assert "amount_paise" not in params
        assert "member_count" not in params
        assert "charge_model" not in params
