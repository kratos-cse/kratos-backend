"""Server-side amount calculation — the ONLY source of truth for what a payer
is charged. A client-supplied amount is never read anywhere in this module.

events.fee is confirmed as a rupee amount (Numeric(10,2) in
alembic/versions/0001_initial_schema.py) — not paise, not a float in the
schema, but Python's Decimal here so no floating-point rounding sneaks into
money math before the final int() conversion.
"""
from decimal import Decimal

from app.models.enums import FeeChargeModel


def compute_amount_paise(fee_rupees: Decimal, charge_model: FeeChargeModel, member_count: int = 1) -> int:
    if fee_rupees < 0:
        raise ValueError(f"Invalid fee: {fee_rupees}")

    paise = round(fee_rupees * 100)
    if charge_model == FeeChargeModel.PER_TEAM:
        return int(paise)

    # PER_MEMBER: TEAM_REGISTRATION charges for one (the leader); TEAM_MEMBER_TOPUP
    # charges for one joining member. member_count is always 1 at both current call
    # sites — accepted as a param rather than hardcoded so a future bulk-charge case
    # doesn't need a rewrite here.
    return int(paise) * member_count
