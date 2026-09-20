"""Server-side amount calculation — the ONLY source of truth for what a payer
is charged. A client-supplied amount is never read anywhere in this module.
"""
from typing import Callable

from .supabase_client import get_db

FeeChargeModel = str  # 'PER_TEAM' | 'PER_MEMBER'


# TODO(events-owner): confirm events.fee's unit and column type (rupees vs paise,
# numeric vs integer). Assumed here: events.fee is a rupee amount, consistent with
# it being an admin-facing "event fee" column rather than an internal paise value.
# If that's wrong, this is the one place to fix it.
def _fetch_fee_rupees(event_id: str) -> float:
    result = get_db().table("events").select("fee").eq("id", event_id).single().execute()
    data = getattr(result, "data", None)
    if not data or data.get("fee") is None:
        raise ValueError(f"Could not load fee for event {event_id}")
    return float(data["fee"])


def compute_amount_paise(
    event_id: str,
    charge_model: FeeChargeModel,
    member_count: int = 1,
    fetch_fee: Callable[[str], float] = _fetch_fee_rupees,
) -> int:
    """fetch_fee is injectable so this stays unit-testable with no database."""
    fee_rupees = fetch_fee(event_id)
    if fee_rupees < 0:
        raise ValueError(f"Invalid fee for event {event_id}: {fee_rupees}")

    paise = round(fee_rupees * 100)
    if charge_model == "PER_TEAM":
        return paise

    # PER_MEMBER: TEAM_REGISTRATION charges for one (the leader); TEAM_MEMBER_TOPUP
    # charges for one joining member. member_count is always 1 at both current call
    # sites — accepted as a param rather than hardcoded so a future bulk-charge case
    # doesn't need a rewrite here.
    return paise * member_count
