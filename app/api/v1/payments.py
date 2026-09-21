import asyncio
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.admin_deps import require_admin_profile, require_super_admin_profile
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.deps_stub import StubProfile, get_current_profile  # TODO: swap for real auth dep
from app.models.enums import PaymentStatus, PaymentType, TeamMemberStatus
from app.models.external_mirrors import Event, EventRegistrationRule, Registration, Team, TeamMember
from app.models.payment import Payment
from app.schemas.payment import (
    CreateOrderRequest,
    CreateOrderResponse,
    PaymentListResponse,
    PaymentOut,
    RefundRequest,
    RefundResponse,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
)
from app.services.amounts import compute_amount_paise
from app.services.payment_apply import SqlAlchemyPaymentsStore, apply_payment_failure, apply_payment_success
from app.services.razorpay_client import get_razorpay
from app.services.refund import RefundError, refund_payment
from app.services.signatures import verify_checkout_signature, verify_webhook_signature

router = APIRouter(tags=["Payments"])


def _load_event_and_rules(db: Session, event_id: uuid.UUID) -> tuple[Event, EventRegistrationRule]:
    event = db.get(Event, event_id)
    if event is None or event.fee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No fee configured for event {event_id}")
    rules = db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id)).scalar_one_or_none()
    if rules is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No registration rules for event {event_id}")
    return event, rules


@router.post("/payments/create-order", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    body: CreateOrderRequest,
    db: Session = Depends(get_db),
    profile: StubProfile = Depends(get_current_profile),
):
    """Server-side amount calculation only — CreateOrderRequest has no amount
    field at all, so there is nothing for a client to supply here.
    payment_type is derived from the registration/team-member row, never
    taken from the client either.

    Atomically claims the registration / team member to prevent duplicate active
    orders under concurrent requests.
    """
    registration: Registration | None = None

    if body.registration_id is not None:
        registration = db.execute(
            select(Registration).where(Registration.id == body.registration_id).with_for_update()
        ).scalar_one_or_none()
        if registration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")
        if registration.payment_id is not None:
            existing_payment = db.get(Payment, registration.payment_id)
            if existing_payment and existing_payment.status in (PaymentStatus.PAID.value, PaymentStatus.CREATED.value):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"This registration already has an active payment or order (status={existing_payment.status})",
                )

        event, rules = _load_event_and_rules(db, registration.event_id)

        if registration.profile_id is not None:
            if registration.profile_id != profile.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
            payment_type = PaymentType.SOLO_REGISTRATION
        else:
            team = db.get(Team, registration.team_id)
            if team is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
            if team.leader_profile_id != profile.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Only the team leader pays for team registration"
                )
            payment_type = PaymentType.TEAM_REGISTRATION
        team_member_id = None

    else:
        team_member = db.execute(
            select(TeamMember).where(TeamMember.id == body.team_member_id).with_for_update()
        ).scalar_one_or_none()
        if team_member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found")
        if team_member.profile_id != profile.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your team membership")
        if team_member.status != TeamMemberStatus.PENDING_PAYMENT.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team member is not pending payment (status={team_member.status})",
            )

        # Enforce one active payment per member
        active_payment = db.execute(
            select(Payment).where(
                Payment.team_member_id == team_member.id,
                Payment.status.in_([PaymentStatus.CREATED.value, PaymentStatus.PAID.value]),
            ).with_for_update()
        ).scalar_one_or_none()
        if active_payment is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active payment order already exists for this team member (status={active_payment.status})",
            )

        event, rules = _load_event_and_rules(db, team_member.event_id)
        payment_type = PaymentType.TEAM_MEMBER_TOPUP
        team_member_id = team_member.id

    charge_model = rules.fee_charge_model  # stored as the enum's string value
    amount_paise = compute_amount_paise(fee_rupees=event.fee, charge_model=charge_model, member_count=1)

    receipt = f"kratos26_{uuid.uuid4().hex[:16]}"
    try:
        order = get_razorpay().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"payment_type": payment_type.value, "event_id": str(event.id)},
            }
        )
    except Exception as err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create Razorpay order: {err}",
        )

    payment = Payment(
        payer_profile_id=profile.id,
        payment_type=payment_type.value,
        team_member_id=team_member_id,
        razorpay_order_id=order["id"],
        amount_paise=amount_paise,
        currency="INR",
        status=PaymentStatus.CREATED.value,
    )
    db.add(payment)
    db.flush()

    if registration is not None:
        registration.payment_id = payment.id

    db.commit()

    return CreateOrderResponse(
        payment_id=payment.id,
        razorpay_order_id=payment.razorpay_order_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
    )


# Fast-path UI confirmation only. This is NOT the source of truth: /webhook is,
# and must independently converge on the same state even if this endpoint is
# never called (browser closed mid-checkout, network drop, etc).
@router.post("/payments/verify", response_model=VerifyPaymentResponse)
def verify_payment(
    body: VerifyPaymentRequest,
    db: Session = Depends(get_db),
    profile: StubProfile = Depends(get_current_profile),
):
    if not settings.has_checkout_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Payment verification service is not configured (missing key secret)",
        )

    payment = db.execute(select(Payment).where(Payment.razorpay_order_id == body.razorpay_order_id)).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown order")
    if payment.payer_profile_id != profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your payment")

    valid = verify_checkout_signature(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
        key_secret=settings.RAZORPAY_KEY_SECRET,
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    store = SqlAlchemyPaymentsStore(db)
    apply_result = apply_payment_success(store, payment.id, body.razorpay_payment_id)

    # Refresh payment from db to return actual persisted status (e.g. if payment was REFUNDED)
    db.refresh(payment)
    persisted_status = PaymentStatus(payment.status)

    return VerifyPaymentResponse(
        payment_id=payment.id,
        applied=apply_result.applied,
        status=persisted_status,
    )


def _process_webhook_sync(event: dict) -> dict:
    """Executed inside a worker thread with its own independent SessionLocal."""
    with SessionLocal() as db:
        entity = (event.get("payload") or {}).get("payment", {}).get("entity", {})
        order_id = entity.get("order_id")
        if not order_id:
            # Signature-valid event we do not act on (a webhook type we do not
            # subscribe to). Acknowledge it so Razorpay stops retrying.
            return {"received": True, "applied": False}

        payment = db.execute(select(Payment).where(Payment.razorpay_order_id == order_id)).scalar_one_or_none()
        if payment is None:
            # Unknown order: nothing in our system to update. Still 200, since
            # retrying will not make the order exist, and Razorpay's retry policy
            # would otherwise hammer us.
            return {"received": True, "applied": False, "reason": "unknown order"}

        store = SqlAlchemyPaymentsStore(db)
        if event.get("event") == "payment.captured":
            result = apply_payment_success(store, payment.id, entity["id"])
            return {"received": True, "applied": result.applied}
        if event.get("event") == "payment.failed":
            result = apply_payment_failure(store, payment.id)
            return {"received": True, "applied": result.applied}

        # Any other event type (refund events, order.paid, etc.): acknowledged,
        # not acted on.
        return {"received": True, "applied": False}


# Source of truth for payment state. Must work even if the user's browser never
# calls /verify. Razorpay retries delivery on non-2xx, so this must be
# idempotent (delegated entirely to apply_payment_success/failure) and must
# return 200 once the signature is valid, even when the event is a no-op.
#
# Only async route in this module — reading the raw request body needs
# `await request.body()`. The DB work is offloaded via asyncio.to_thread with
# its own thread-safe SessionLocal.
@router.post("/payments/webhook")
async def payments_webhook(request: Request):
    if not settings.has_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook service is not configured (missing webhook secret)",
        )

    # Read as raw bytes FIRST. Hashing after json-parsing and re-serializing
    # will never match Razorpay's signature over the original wire bytes.
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    if not signature:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing X-Razorpay-Signature")

    valid = verify_webhook_signature(
        raw_body=raw_body, signature=signature, webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET
    )
    if not valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    try:
        event = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON")

    return await asyncio.to_thread(_process_webhook_sync, event)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(
    payment_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile: StubProfile = Depends(get_current_profile),
):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payment.payer_profile_id != profile.id and not profile.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your payment")

    return payment


@router.get("/admin/payments", response_model=PaymentListResponse)
def list_payments(
    db: Session = Depends(get_db),
    _admin: StubProfile = Depends(require_admin_profile),
    payment_id: Optional[uuid.UUID] = None,
    participant_id: Optional[uuid.UUID] = None,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    type: Optional[PaymentType] = None,
    status: Optional[PaymentStatus] = None,
    page: int = 1,
    page_size: int = 25,
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    query = select(Payment)
    if payment_id:
        query = query.where(Payment.id == payment_id)
    if participant_id:
        query = query.where(Payment.payer_profile_id == participant_id)
    if razorpay_order_id:
        query = query.where(Payment.razorpay_order_id == razorpay_order_id)
    if razorpay_payment_id:
        query = query.where(Payment.razorpay_payment_id == razorpay_payment_id)
    if type:
        query = query.where(Payment.payment_type == type.value)
    if status:
        query = query.where(Payment.status == status.value)

    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()

    start = (page - 1) * page_size
    payments = list(
        db.execute(query.order_by(Payment.created_at.desc()).offset(start).limit(page_size)).scalars().all()
    )

    return PaymentListResponse(
        payments=[PaymentOut.model_validate(p) for p in payments], page=page, page_size=page_size, total=total
    )


@router.post("/admin/payments/{payment_id}/refund", response_model=RefundResponse)
def refund(
    payment_id: uuid.UUID,
    body: RefundRequest,
    db: Session = Depends(get_db),
    _super_admin: StubProfile = Depends(require_super_admin_profile),
):
    try:
        result = refund_payment(db, payment_id, body.reason)
    except RefundError as err:
        raise HTTPException(status_code=err.status, detail=str(err))
    return RefundResponse(
        payment_id=result.payment_id, refund_id=result.refund_id, refund_amount_paise=result.refund_amount_paise
    )
