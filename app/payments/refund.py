"""§6 refund flow: Razorpay refund -> update payments -> linked-entity state ->
notification, in that order. The route handler is responsible for the
super-admin authorization check — this function assumes it has already been
done.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from .handoffs import send_notification
from .razorpay_client import get_razorpay, with_retry
from .supabase_client import get_db

# TODO(registrations-teams-owner): §6.3 of the brief is explicit that these exact
# enum values and the transition rule must come from whoever owns registrations/
# teams, not be assumed here. Placeholder values below — do NOT ship a real refund
# flow against these without that confirmation.
REFUND_LINKED_STATES = {
    "SOLO_REGISTRATION": {"table": "registrations", "status": "CANCELLED"},
    "TEAM_REGISTRATION": {"table": "teams", "status": "CANCELLED"},
    "TEAM_MEMBER_TOPUP": {"table": "team_members", "status": "REMOVED"},
}


class RefundError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class RefundResult:
    payment_id: str
    refund_id: str
    refund_amount_paise: int


def refund_payment(payment_id: str, reason: str) -> RefundResult:
    db = get_db()

    fetch = (
        db.table("payments")
        .select("id, payment_type, payer_profile_id, amount_paise, razorpay_payment_id, status, team_member_id")
        .eq("id", payment_id)
        .single()
        .execute()
    )
    payment = fetch.data
    if not payment:
        raise RefundError(f"Payment {payment_id} not found", status=404)
    if payment["status"] != "PAID":
        raise RefundError(f"Payment {payment_id} is not PAID (status={payment['status']})", status=409)
    if not payment.get("razorpay_payment_id"):
        raise RefundError(f"Payment {payment_id} has no razorpay_payment_id to refund", status=409)

    # 1. Razorpay refund.
    refund = with_retry(
        lambda: get_razorpay().payment.refund(payment["razorpay_payment_id"], {"amount": payment["amount_paise"]})
    )

    # 2. payments row. Conditional on still being PAID, mirroring the idempotency
    # guard in apply.py — a duplicate refund click cannot double-apply.
    update = (
        db.table("payments")
        .update(
            {
                "status": "REFUNDED",
                "refund_id": refund["id"],
                "refund_amount_paise": int(refund["amount"]),
                "refunded_at": datetime.now(timezone.utc).isoformat(),
                "refund_reason": reason,
            }
        )
        .eq("id", payment_id)
        .eq("status", "PAID")
        .execute()
    )
    if not update.data:
        # The Razorpay refund already succeeded even though our row didn't flip (a
        # concurrent refund attempt beat us to it) — surface this loudly rather than
        # silently swallowing a real refund that isn't reflected in our records.
        raise RefundError(
            f"Razorpay refund {refund['id']} succeeded but payments row update failed for {payment_id} — "
            "reconcile manually",
            status=500,
        )

    # 3. Linked registration/team-member state — see REFUND_LINKED_STATES TODO above.
    linked = REFUND_LINKED_STATES[payment["payment_type"]]
    if payment["payment_type"] == "TEAM_MEMBER_TOPUP" and payment.get("team_member_id"):
        db.table(linked["table"]).update({"status": linked["status"]}).eq("id", payment["team_member_id"]).execute()
    else:
        db.table(linked["table"]).update({"status": linked["status"]}).eq("payment_id", payment["id"]).execute()

    # 4. Notification handoff.
    send_notification(
        kind="REFUND_ISSUED",
        payer_profile_id=payment["payer_profile_id"],
        amount_paise=int(refund["amount"]),
        reason=reason,
    )

    return RefundResult(payment_id=payment["id"], refund_id=refund["id"], refund_amount_paise=int(refund["amount"]))
