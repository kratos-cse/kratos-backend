"""The ONLY place payment status changes. /verify and /webhook both call
apply_payment_success/apply_payment_failure — never duplicate this transition
logic in a route handler.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from .handoffs import issue_receipt, send_notification, trigger_qr
from .supabase_client import get_db

PaymentType = str  # 'TEAM_REGISTRATION' | 'SOLO_REGISTRATION' | 'TEAM_MEMBER_TOPUP'


@dataclass
class PaymentRow:
    id: str
    payment_type: PaymentType
    team_member_id: Optional[str]
    payer_profile_id: str
    amount_paise: int


@dataclass
class ApplyResult:
    applied: bool
    payment: Optional[PaymentRow]


class PaymentsStore(Protocol):
    """Narrow seam between the state-transition logic below and actual
    storage, so apply_payment_success/failure can be driven by a small
    in-memory stub in tests without faking the full Supabase query-builder
    chain."""

    def transition_payment_status(
        self, payment_id: str, from_statuses: list[str], patch: dict
    ) -> Optional[PaymentRow]: ...

    def confirm_solo_registration(self, payment_id: str) -> None: ...

    def confirm_team_registration(self, payment_id: str) -> None: ...

    def confirm_team_member_topup(self, team_member_id: str) -> None: ...


class SupabasePaymentsStore:
    """Real, Supabase-backed store used by every route. Tests pass their own
    PaymentsStore instead of touching this."""

    def transition_payment_status(
        self, payment_id: str, from_statuses: list[str], patch: dict
    ) -> Optional[PaymentRow]:
        result = (
            get_db()
            .table("payments")
            .update({**patch, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", payment_id)
            .in_("status", from_statuses)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        row = rows[0]
        return PaymentRow(
            id=row["id"],
            payment_type=row["payment_type"],
            team_member_id=row.get("team_member_id"),
            payer_profile_id=row["payer_profile_id"],
            amount_paise=row["amount_paise"],
        )

    def confirm_solo_registration(self, payment_id: str) -> None:
        get_db().table("registrations").update({"status": "CONFIRMED"}).eq("payment_id", payment_id).execute()

    # TODO(registrations-teams-owner): there is no documented FK from `payments` to
    # `teams` for a TEAM_REGISTRATION payment. Assumed here: the team leader's
    # `registrations` row carries this payment_id (mirroring the solo case) and also
    # carries `team_id` + `profile_id`, so the team and leader can be found through
    # it. Confirm this shape (or the real one) with whoever owns registrations/teams
    # before trusting this in production — do not guess silently past this point.
    def confirm_team_registration(self, payment_id: str) -> None:
        db = get_db()
        result = (
            db.table("registrations").select("team_id, profile_id").eq("payment_id", payment_id).single().execute()
        )
        registration = result.data
        if not registration:
            raise RuntimeError(
                f"confirm_team_registration: no registration found for payment {payment_id} — "
                "assumed registrations.payment_id/team_id linkage may not match the real schema."
            )
        team_id = registration["team_id"]
        profile_id = registration["profile_id"]

        db.table("teams").update({"status": "PAID"}).eq("id", team_id).execute()
        db.table("team_members").update({"status": "ACTIVE"}).eq("team_id", team_id).eq(
            "profile_id", profile_id
        ).execute()

    def confirm_team_member_topup(self, team_member_id: str) -> None:
        get_db().table("team_members").update({"status": "ACTIVE"}).eq("id", team_member_id).execute()


_default_store = SupabasePaymentsStore()


def _run_handoffs(payment: PaymentRow) -> None:
    if payment.payment_type == "SOLO_REGISTRATION":
        trigger_qr(registration_id=payment.id)
    elif payment.payment_type == "TEAM_MEMBER_TOPUP" and payment.team_member_id:
        trigger_qr(team_member_id=payment.team_member_id)
    # TEAM_REGISTRATION does not issue a QR by itself — joining members get theirs
    # on their own TEAM_MEMBER_TOPUP or free-join confirmation (see brief §4).

    send_notification(kind="PAYMENT_CONFIRMED", payer_profile_id=payment.payer_profile_id, amount_paise=payment.amount_paise)
    issue_receipt(payment_id=payment.id)


def apply_payment_success(
    payment_id: str, razorpay_payment_id: str, store: PaymentsStore = _default_store
) -> ApplyResult:
    # Guard is CREATED|FAILED, not "!= PAID": that stops a late-arriving retry from
    # flipping an already-REFUNDED payment back to PAID, while still allowing the
    # real case where payment.failed lands first and the user successfully retries
    # on the same order.
    payment = store.transition_payment_status(
        payment_id,
        ["CREATED", "FAILED"],
        {"status": "PAID", "razorpay_payment_id": razorpay_payment_id},
    )
    if payment is None:
        return ApplyResult(applied=False, payment=None)

    if payment.payment_type == "SOLO_REGISTRATION":
        store.confirm_solo_registration(payment.id)
    elif payment.payment_type == "TEAM_REGISTRATION":
        store.confirm_team_registration(payment.id)
    elif payment.payment_type == "TEAM_MEMBER_TOPUP":
        if not payment.team_member_id:
            raise RuntimeError(f"TEAM_MEMBER_TOPUP payment {payment.id} is missing team_member_id")
        store.confirm_team_member_topup(payment.team_member_id)

    _run_handoffs(payment)
    return ApplyResult(applied=True, payment=payment)


def apply_payment_failure(payment_id: str, store: PaymentsStore = _default_store) -> ApplyResult:
    # Only CREATED -> FAILED. A PAID or REFUNDED payment must never be moved to
    # FAILED by a stray/duplicate failure webhook.
    payment = store.transition_payment_status(payment_id, ["CREATED"], {"status": "FAILED"})
    return ApplyResult(applied=payment is not None, payment=payment)
