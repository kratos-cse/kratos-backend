import pytest

from app.payments.amounts import compute_amount_paise


class TestComputeAmountPaise:
    def test_converts_per_team_rupee_fee_to_integer_paise(self):
        amount = compute_amount_paise("e1", "PER_TEAM", fetch_fee=lambda _event_id: 500)
        assert amount == 50000
        assert isinstance(amount, int)

    def test_multiplies_per_member_fee_by_member_count(self):
        amount = compute_amount_paise("e1", "PER_MEMBER", member_count=1, fetch_fee=lambda _event_id: 250)
        assert amount == 25000

    def test_defaults_member_count_to_one_for_per_member(self):
        amount = compute_amount_paise("e1", "PER_MEMBER", fetch_fee=lambda _event_id: 100)
        assert amount == 10000

    def test_never_produces_a_float_fractional_fee_rounds_to_whole_paise(self):
        amount = compute_amount_paise("e1", "PER_TEAM", fetch_fee=lambda _event_id: 99.999)
        assert isinstance(amount, int)
        assert amount == 10000

    def test_rejects_a_negative_fee_rather_than_charging_it(self):
        with pytest.raises(ValueError):
            compute_amount_paise("e1", "PER_TEAM", fetch_fee=lambda _event_id: -50)

    def test_signature_has_no_client_supplied_amount_field(self):
        # compute_amount_paise's parameters are event_id, charge_model, member_count,
        # fetch_fee — there is no "amount" parameter for a caller to smuggle a
        # pre-computed charge through.
        import inspect

        params = set(inspect.signature(compute_amount_paise).parameters)
        assert "amount" not in params
        assert "amount_paise" not in params
