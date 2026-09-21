from decimal import Decimal
import pytest

from app.models.enums import FeeChargeModel
from app.services.amounts import compute_amount_paise


class TestComputeAmountPaise:
    def test_per_team_charges_flat_paise_regardless_of_member_count(self):
        fee = Decimal("150.00")
        assert compute_amount_paise(fee, FeeChargeModel.PER_TEAM, member_count=1) == 15000
        assert compute_amount_paise(fee, FeeChargeModel.PER_TEAM, member_count=4) == 15000

    def test_per_member_multiplies_by_member_count(self):
        fee = Decimal("75.50")
        assert compute_amount_paise(fee, FeeChargeModel.PER_MEMBER, member_count=1) == 7550
        assert compute_amount_paise(fee, FeeChargeModel.PER_MEMBER, member_count=3) == 22650

    def test_zero_fee_is_allowed(self):
        assert compute_amount_paise(Decimal("0.00"), FeeChargeModel.PER_TEAM) == 0
        assert compute_amount_paise(Decimal("0.00"), FeeChargeModel.PER_MEMBER, member_count=2) == 0

    def test_negative_fee_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid fee"):
            compute_amount_paise(Decimal("-10.00"), FeeChargeModel.PER_TEAM)
