import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .amounts import compute_amount_paise
from .apply import apply_payment_failure, apply_payment_success
from .auth import get_caller_profile_id, get_caller_role, require_admin, require_super_admin
from .env import env
from .razorpay_client import get_razorpay, with_retry
from .refund import RefundError, refund_payment
from .signatures import verify_checkout_signature, verify_webhook_signature
from .supabase_client import get_db

router = APIRouter()

PaymentType = Literal["TEAM_REGISTRATION", "SOLO_REGISTRATION", "TEAM_MEMBER_TOPUP"]


class CreateOrderBody(BaseModel):
    event_id: str
    payment_type: PaymentType
    team_member_id: Optional[str] = None  # required for TEAM_MEMBER_TOPUP


class VerifyBody(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class RefundBody(BaseModel):
    reason: str


@router.post("/payments/create-order")
def create_order(body: CreateOrderBody, payer_profile_id: str = Depends(get_caller_profile_id)):
    if body.payment_type == "TEAM_MEMBER_TOPUP" and not body.team_member_id:
        raise HTTPException(status_code=400, detail="team_member_id is required for TEAM_MEMBER_TOPUP")

    db = get_db()
    rules = (
        db.table("event_registration_rules")
        .select("fee_charge_model")
        .eq("event_id", body.event_id)
        .single()
        .execute()
    )
    if not rules.data:
        raise HTTPException(status_code=404, detail=f"No fee rules for event {body.event_id}")

    # Amount is computed entirely server-side. CreateOrderBody has no amount
    # field at all, so there is nothing for a client to supply here.
    amount_paise = compute_amount_paise(
        event_id=body.event_id, charge_model=rules.data["fee_charge_model"], member_count=1
    )

    receipt = f"kratos26_{uuid.uuid4().hex[:16]}"
    order = with_retry(
        lambda: get_razorpay().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"payment_type": body.payment_type, "event_id": body.event_id},
            }
        )
    )

    inserted = (
        db.table("payments")
        .insert(
            {
                "payer_profile_id": payer_profile_id,
                "payment_type": body.payment_type,
                "team_member_id": body.team_member_id if body.payment_type == "TEAM_MEMBER_TOPUP" else None,
                "razorpay_order_id": order["id"],
                "amount_paise": amount_paise,
                "currency": "INR",
                "status": "CREATED",
            }
        )
        .execute()
    )
    if not inserted.data:
        raise HTTPException(status_code=500, detail="Failed to record payment")
    payment = inserted.data[0]

    return {
        "paymentId": payment["id"],
        "razorpayOrderId": payment["razorpay_order_id"],
        "amountPaise": payment["amount_paise"],
        "currency": payment["currency"],
        "razorpayKeyId": env("RAZORPAY_KEY_ID"),
    }


# Fast-path UI confirmation only. This is NOT the source of truth: /webhook is,
# and must independently converge on the same state even if this endpoint is
# never called (browser closed mid-checkout, network drop, etc).
@router.post("/payments/verify")
def verify_payment(body: VerifyBody, caller_profile_id: str = Depends(get_caller_profile_id)):
    db = get_db()
    fetch = (
        db.table("payments")
        .select("id, payer_profile_id, status")
        .eq("razorpay_order_id", body.razorpay_order_id)
        .single()
        .execute()
    )
    payment = fetch.data
    if not payment:
        raise HTTPException(status_code=404, detail="Unknown order")
    if payment["payer_profile_id"] != caller_profile_id:
        raise HTTPException(status_code=403, detail="Not your payment")

    valid = verify_checkout_signature(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
        key_secret=env("RAZORPAY_KEY_SECRET"),
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid signature")

    result = apply_payment_success(payment["id"], body.razorpay_payment_id)
    return {"paymentId": payment["id"], "applied": result.applied, "status": "PAID"}


# Source of truth for payment state. Must work even if the user's browser never
# calls /verify. Razorpay retries delivery on non-2xx, so this must be
# idempotent (delegated entirely to apply_payment_success/failure) and must
# return 200 once the signature is valid, even when the event is a no-op.
@router.post("/payments/webhook")
async def payments_webhook(request: Request):
    # Read as raw bytes FIRST. Hashing after json-parsing and re-serializing
    # will never match Razorpay's signature over the original wire bytes.
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Signature")

    valid = verify_webhook_signature(
        raw_body=raw_body, signature=signature, webhook_secret=env("RAZORPAY_WEBHOOK_SECRET")
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    entity = (event.get("payload") or {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")
    if not order_id:
        # Signature-valid event we do not act on (a webhook type we do not
        # subscribe to). Acknowledge it so Razorpay stops retrying.
        return {"received": True, "applied": False}

    db = get_db()
    fetch = db.table("payments").select("id").eq("razorpay_order_id", order_id).single().execute()
    payment = fetch.data
    if not payment:
        # Unknown order: nothing in our system to update. Still 200, since
        # retrying will not make the order exist, and Razorpay's retry policy
        # would otherwise hammer us.
        return {"received": True, "applied": False, "reason": "unknown order"}

    if event.get("event") == "payment.captured":
        result = apply_payment_success(payment["id"], entity["id"])
        return {"received": True, "applied": result.applied}
    if event.get("event") == "payment.failed":
        result = apply_payment_failure(payment["id"])
        return {"received": True, "applied": result.applied}

    # Any other event type (refund events, order.paid, etc.): acknowledged,
    # not acted on.
    return {"received": True, "applied": False}


@router.get("/payments/{payment_id}")
def get_payment(payment_id: str, caller_profile_id: str = Depends(get_caller_profile_id)):
    db = get_db()
    fetch = db.table("payments").select("*").eq("id", payment_id).single().execute()
    payment = fetch.data
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment["payer_profile_id"] != caller_profile_id:
        role = get_caller_role(caller_profile_id)
        if role not in ("ADMIN", "SUPER_ADMIN"):
            raise HTTPException(status_code=403, detail="Not your payment")

    return payment


# Search/filter by participant, event, team, payment ID, Razorpay IDs, type,
# status. Participant filtering here is by payer_profile_id directly. Event/
# team filtering would join through registrations/teams, which this module
# does not own, so those params are not wired up yet: a TODO once
# registrations/teams expose a stable join key.
@router.get("/admin/payments")
def list_payments(
    payment_id: Optional[str] = None,
    participant_id: Optional[str] = None,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    type: Optional[PaymentType] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    _admin_profile_id: str = Depends(require_admin),
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    query = get_db().table("payments").select("*", count="exact")
    if payment_id:
        query = query.eq("id", payment_id)
    if participant_id:
        query = query.eq("payer_profile_id", participant_id)
    if razorpay_order_id:
        query = query.eq("razorpay_order_id", razorpay_order_id)
    if razorpay_payment_id:
        query = query.eq("razorpay_payment_id", razorpay_payment_id)
    if type:
        query = query.eq("payment_type", type)
    if status:
        query = query.eq("status", status)

    start = (page - 1) * page_size
    end = start + page_size - 1
    result = query.order("created_at", desc=True).range(start, end).execute()

    return {"payments": result.data, "page": page, "pageSize": page_size, "total": result.count or 0}


@router.post("/admin/payments/{payment_id}/refund")
def refund(payment_id: str, body: RefundBody, _super_admin_profile_id: str = Depends(require_super_admin)):
    if not body.reason:
        raise HTTPException(status_code=400, detail="reason is required")
    try:
        result = refund_payment(payment_id, body.reason)
    except RefundError as err:
        raise HTTPException(status_code=err.status, detail=str(err))
    return {
        "paymentId": result.payment_id,
        "refundId": result.refund_id,
        "refundAmountPaise": result.refund_amount_paise,
    }
