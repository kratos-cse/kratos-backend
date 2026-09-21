from decimal import Decimal

import pytest

from app.models.enums import FeeChargeModel
from app.services.amounts import compute_amount_paise


class TestComputeAmountPaise:
    def test_converts_per_team_rupee_fee_to_integer_paise(self):
        amount = compute_amount_paise(Decimal("500"), FeeChargeModel.PER_TEAM)
        assert amount == 50000
        assert isinstance(amount, int)

    def test_multiplies_per_member_fee_by_member_count(self):
        amount = compute_amount_paise(Decimal("250"), FeeChargeModel.PER_MEMBER, member_count=1)
        assert amount == 25000

    def test_defaults_member_count_to_one_for_per_member(self):
        amount = compute_amount_paise(Decimal("100"), FeeChargeModel.PER_MEMBER)
        assert amount == 10000

    def test_never_produces_a_float_fractional_fee_rounds_to_whole_paise(self):
        amount = compute_amount_paise(Decimal("99.999"), FeeChargeModel.PER_TEAM)
        assert isinstance(amount, int)
        assert amount == 10000

    def test_rejects_a_negative_fee_rather_than_charging_it(self):
        with pytest.raises(ValueError):
            compute_amount_paise(Decimal("-50"), FeeChargeModel.PER_TEAM)

    def test_signature_has_no_client_supplied_amount_field(self):
        import inspect

        params = set(inspect.signature(compute_amount_paise).parameters)
        assert "amount" not in params
        assert "amount_paise" not in params
