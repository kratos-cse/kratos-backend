import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile, get_current_user
from app.db.session import get_db
from app.models.enums import PaymentStatus, PaymentType, TeamMemberStatus
from app.models.event import Event, EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.models.user import User
from app.schemas.payment import (
    CreateOrderRequest,
    CreateOrderResponse,
    PaymentOut,
    VerifyPaymentRequest,
    VerifyPaymentResponse,
)
from app.core.config import settings
from app.services.amounts import compute_amount_paise
from app.services.payment_apply import SqlAlchemyPaymentsStore, apply_payment_failure, apply_payment_success
from app.services.razorpay_client import get_razorpay, with_retry
from app.services.signatures import verify_checkout_signature, verify_webhook_signature

router = APIRouter(tags=["Payments"])


async def _load_event_and_rules(db: AsyncSession, event_id: uuid.UUID) -> tuple[Event, EventRegistrationRule]:
    event = await db.get(Event, event_id)
    if event is None or event.fee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No fee configured for event {event_id}")
    rules_result = await db.execute(select(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id))
    rules = rules_result.scalar_one_or_none()
    if rules is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No registration rules for event {event_id}")
    return event, rules


@router.post("/payments/create-order", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    """Server-side amount calculation only — CreateOrderRequest has no amount
    field at all, so there is nothing for a client to supply here.
    payment_type is derived from the registration/team-member row, never
    taken from the client either.
    """
    registration: Registration | None = None
    team_member: TeamMember | None = None

    if body.registration_id is not None:
        registration = await db.get(Registration, body.registration_id)
        if registration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")
        if registration.payment_id is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This registration already has a payment")

        event, rules = await _load_event_and_rules(db, registration.event_id)

        if registration.profile_id is not None:
            if registration.profile_id != profile.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your registration")
            payment_type = PaymentType.SOLO_REGISTRATION
        else:
            team = await db.get(Team, registration.team_id)
            if team is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
            if team.leader_profile_id != profile.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Only the team leader pays for team registration"
                )
            payment_type = PaymentType.TEAM_REGISTRATION
        team_member_id = None

    else:
        team_member = await db.get(TeamMember, body.team_member_id)
        if team_member is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team member not found")
        if team_member.profile_id != profile.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your team membership")
        if team_member.status != TeamMemberStatus.PENDING_PAYMENT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team member is not pending payment (status={team_member.status})",
            )
        event, rules = await _load_event_and_rules(db, team_member.event_id)
        payment_type = PaymentType.TEAM_MEMBER_TOPUP
        team_member_id = team_member.id

    amount_paise = compute_amount_paise(fee_rupees=event.fee, charge_model=rules.fee_charge_model, member_count=1)

    receipt = f"kratos26_{uuid.uuid4().hex[:16]}"
    order = await with_retry(
        lambda: get_razorpay().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {"payment_type": payment_type.value, "event_id": str(event.id)},
            }
        )
    )

    payment = Payment(
        payer_profile_id=profile.id,
        payment_type=payment_type,
        team_member_id=team_member_id,
        razorpay_order_id=order["id"],
        amount_paise=amount_paise,
        currency="INR",
        status=PaymentStatus.CREATED,
    )
    db.add(payment)
    await db.flush()

    if registration is not None:
        registration.payment_id = payment.id

    await db.commit()

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
async def verify_payment(
    body: VerifyPaymentRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    result = await db.execute(select(Payment).where(Payment.razorpay_order_id == body.razorpay_order_id))
    payment = result.scalar_one_or_none()
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
    apply_result = await apply_payment_success(store, payment.id, body.razorpay_payment_id)
    return VerifyPaymentResponse(payment_id=payment.id, applied=apply_result.applied, status=PaymentStatus.PAID)


# Source of truth for payment state. Must work even if the user's browser never
# calls /verify. Razorpay retries delivery on non-2xx, so this must be
# idempotent (delegated entirely to apply_payment_success/failure) and must
# return 200 once the signature is valid, even when the event is a no-op.
@router.post("/payments/webhook")
async def payments_webhook(request: Request, db: AsyncSession = Depends(get_db)):
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

    entity = (event.get("payload") or {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")
    if not order_id:
        # Signature-valid event we do not act on (a webhook type we do not
        # subscribe to). Acknowledge it so Razorpay stops retrying.
        return {"received": True, "applied": False}

    result = await db.execute(select(Payment).where(Payment.razorpay_order_id == order_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        # Unknown order: nothing in our system to update. Still 200, since
        # retrying will not make the order exist, and Razorpay's retry policy
        # would otherwise hammer us.
        return {"received": True, "applied": False, "reason": "unknown order"}

    store = SqlAlchemyPaymentsStore(db)
    if event.get("event") == "payment.captured":
        apply_result = await apply_payment_success(store, payment.id, entity["id"])
        return {"received": True, "applied": apply_result.applied}
    if event.get("event") == "payment.failed":
        apply_result = await apply_payment_failure(store, payment.id)
        return {"received": True, "applied": apply_result.applied}

    # Any other event type (refund events, order.paid, etc.): acknowledged,
    # not acted on.
    return {"received": True, "applied": False}


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def get_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
    current_user: User = Depends(get_current_user),
):
    payment = await db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payment.payer_profile_id != profile.id and not current_user.is_admin_flagged:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your payment")

    return payment
