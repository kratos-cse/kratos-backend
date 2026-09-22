"""Payment HTTP routes — async SQLAlchemy + Authentication JWT deps."""
import json
import uuid
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_admin import require_super_admin
from app.core.config import settings
from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.enums import PaymentStatus, PaymentType
from app.models.event import EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team
from app.payments.amounts import compute_amount_paise
from app.payments.apply import apply_payment_failure, apply_payment_success
from app.payments.razorpay_client import get_razorpay, with_retry
from app.payments.refund import RefundError, refund_payment
from app.payments.signatures import verify_checkout_signature, verify_webhook_signature

router = APIRouter(tags=["Payments"])

PaymentTypeLiteral = Literal["TEAM_REGISTRATION", "SOLO_REGISTRATION"]


class CreateOrderBody(BaseModel):
    event_id: UUID
    payment_type: PaymentTypeLiteral
    registration_id: Optional[UUID] = None


class VerifyBody(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class RefundBody(BaseModel):
    reason: str


async def _link_registration_payment(
    db: AsyncSession,
    *,
    payment: Payment,
    event_id: UUID,
    payer: Profile,
    payment_type: PaymentType,
    registration_id: Optional[UUID],
) -> None:
    if registration_id:
        result = await db.execute(select(Registration).where(Registration.id == registration_id))
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="Registration not found")
        if registration.event_id != event_id:
            raise HTTPException(status_code=400, detail="Registration does not belong to this event")
    elif payment_type == PaymentType.SOLO_REGISTRATION:
        result = await db.execute(
            select(Registration).where(
                Registration.event_id == event_id,
                Registration.profile_id == payer.id,
                Registration.payment_id.is_(None),
            )
        )
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="No pending solo registration for this event")
    else:  # TEAM_REGISTRATION
        team_result = await db.execute(
            select(Team).where(Team.event_id == event_id, Team.leader_profile_id == payer.id)
        )
        team = team_result.scalar_one_or_none()
        if not team:
            raise HTTPException(status_code=404, detail="No team found for payer on this event")
        result = await db.execute(
            select(Registration).where(
                Registration.team_id == team.id,
                Registration.payment_id.is_(None),
            )
        )
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="No pending team registration for this event")

    registration.payment_id = payment.id


@router.post("/payments/create-order")
async def create_order(
    body: CreateOrderBody,
    db: AsyncSession = Depends(get_db),
    payer: Profile = Depends(get_current_profile),
):
    rules_result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == body.event_id)
    )
    rules = rules_result.scalar_one_or_none()
    if not rules:
        raise HTTPException(status_code=404, detail=f"No fee rules for event {body.event_id}")

    payment_type = PaymentType(body.payment_type)

    try:
        amount_paise = await compute_amount_paise(db, body.event_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    receipt = f"kratos26_{uuid.uuid4().hex[:16]}"
    order = with_retry(
        lambda: get_razorpay().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    "payment_type": body.payment_type,
                    "event_id": str(body.event_id),
                },
            }
        )
    )

    payment = Payment(
        payer_profile_id=payer.id,
        payment_type=payment_type,
        team_member_id=None,
        razorpay_order_id=order["id"],
        amount_paise=amount_paise,
        currency="INR",
        status=PaymentStatus.CREATED,
    )
    db.add(payment)
    await db.flush()

    await _link_registration_payment(
        db,
        payment=payment,
        event_id=body.event_id,
        payer=payer,
        payment_type=payment_type,
        registration_id=body.registration_id,
    )

    from app.services import audit_service

    await audit_service.log_activity(
        db,
        action="PAYMENT_ORDER_CREATED",
        resource_type="PAYMENT",
        resource_id=payment.id,
        actor_user_id=payer.user_id,
        actor_profile_id=payer.id,
        actor_role="PARTICIPANT",
        status="SUCCESS",
        details={
            "event_id": str(body.event_id),
            "payment_type": body.payment_type,
            "amount_paise": amount_paise,
            "razorpay_order_id": payment.razorpay_order_id,
            "registration_id": str(body.registration_id) if body.registration_id else None,
        },
    )

    await db.commit()
    await db.refresh(payment)

    return {
        "paymentId": str(payment.id),
        "razorpayOrderId": payment.razorpay_order_id,
        "amountPaise": payment.amount_paise,
        "currency": payment.currency,
        "razorpayKeyId": settings.RAZORPAY_KEY_ID,
    }


@router.post("/payments/verify")
async def verify_payment(
    body: VerifyBody,
    db: AsyncSession = Depends(get_db),
    payer: Profile = Depends(get_current_profile),
):
    from app.services import audit_service

    result = await db.execute(
        select(Payment).where(Payment.razorpay_order_id == body.razorpay_order_id)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Unknown order")
    if payment.payer_profile_id != payer.id:
        raise HTTPException(status_code=403, detail="Not your payment")

    if not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=500, detail="RAZORPAY_KEY_SECRET is not configured")

    valid = verify_checkout_signature(
        order_id=body.razorpay_order_id,
        payment_id=body.razorpay_payment_id,
        signature=body.razorpay_signature,
        key_secret=settings.RAZORPAY_KEY_SECRET,
    )
    if not valid:
        await audit_service.log_activity(
            db,
            action="PAYMENT_VERIFY_FAILED",
            resource_type="PAYMENT",
            resource_id=payment.id,
            actor_user_id=payer.user_id,
            actor_profile_id=payer.id,
            actor_role="PARTICIPANT",
            status="FAILURE",
            details={
                "razorpay_order_id": body.razorpay_order_id,
                "razorpay_payment_id": body.razorpay_payment_id,
                "reason": "Invalid Razorpay checkout signature",
            },
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid signature")

    await audit_service.log_activity(
        db,
        action="PAYMENT_VERIFY_SUCCESS",
        resource_type="PAYMENT",
        resource_id=payment.id,
        actor_user_id=payer.user_id,
        actor_profile_id=payer.id,
        actor_role="PARTICIPANT",
        status="SUCCESS",
        details={
            "razorpay_order_id": body.razorpay_order_id,
            "razorpay_payment_id": body.razorpay_payment_id,
        },
    )

    applied = await apply_payment_success(db, payment.id, body.razorpay_payment_id)
    return {"paymentId": str(payment.id), "applied": applied.applied, "status": "PAID"}


@router.post("/payments/webhook")
async def payments_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    from app.services import audit_service

    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Signature")
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="RAZORPAY_WEBHOOK_SECRET is not configured")

    valid = verify_webhook_signature(
        raw_body=raw_body,
        signature=signature,
        webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET,
    )
    if not valid:
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON")

    entity = (event.get("payload") or {}).get("payment", {}).get("entity", {})
    order_id = entity.get("order_id")
    if not order_id:
        return {"received": True, "applied": False}

    result = await db.execute(select(Payment).where(Payment.razorpay_order_id == order_id))
    payment = result.scalar_one_or_none()
    if not payment:
        return {"received": True, "applied": False, "reason": "unknown order"}

    await audit_service.log_activity(
        db,
        action="PAYMENT_WEBHOOK_RECEIVED",
        resource_type="PAYMENT",
        resource_id=payment.id,
        actor_role="WEBHOOK",
        status="SUCCESS",
        details={
            "event": event.get("event"),
            "order_id": order_id,
            "razorpay_payment_id": entity.get("id"),
        },
    )

    if event.get("event") == "payment.captured":
        applied = await apply_payment_success(db, payment.id, entity["id"])
        return {"received": True, "applied": applied.applied}
    if event.get("event") == "payment.failed":
        applied = await apply_payment_failure(db, payment.id)
        return {"received": True, "applied": applied.applied}

    return {"received": True, "applied": False}


async def _sync_payment_if_needed(db: AsyncSession, payment: Payment) -> Payment:
    if (
        payment.status == PaymentStatus.CREATED
        and payment.razorpay_order_id
        and settings.RAZORPAY_KEY_ID
        and settings.RAZORPAY_KEY_SECRET
    ):
        try:
            order_payments = with_retry(lambda: get_razorpay().order.payments(payment.razorpay_order_id))
            items = order_payments.get("items", [])
            for item in items:
                if item.get("status") in ("captured", "authorized"):
                    applied = await apply_payment_success(db, payment.id, item["id"])
                    if applied.applied:
                        await db.refresh(payment)
                        break
        except Exception:
            pass
    return payment


@router.get("/payments/{payment_id}")
async def get_payment(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
    payer: Profile = Depends(get_current_profile),
):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.payer_profile_id != payer.id:
        admin_result = await db.execute(
            select(AdminUser).where(AdminUser.user_id == payer.user_id, AdminUser.is_active.is_(True))
        )
        if admin_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Not your payment")

    payment = await _sync_payment_if_needed(db, payment)

    return {
        "id": str(payment.id),
        "payer_profile_id": str(payment.payer_profile_id),
        "payment_type": payment.payment_type.value,
        "team_member_id": str(payment.team_member_id) if payment.team_member_id else None,
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_payment_id": payment.razorpay_payment_id,
        "amount_paise": payment.amount_paise,
        "currency": payment.currency,
        "status": payment.status.value,
        "refund_id": payment.refund_id,
        "refund_amount_paise": payment.refund_amount_paise,
        "refunded_at": payment.refunded_at,
        "refund_reason": payment.refund_reason,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


@router.post("/payments/{payment_id}/sync")
async def sync_payment_status(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
    payer: Profile = Depends(get_current_profile),
):
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.payer_profile_id != payer.id:
        admin_result = await db.execute(
            select(AdminUser).where(AdminUser.user_id == payer.user_id, AdminUser.is_active.is_(True))
        )
        if admin_result.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Not your payment")

    payment = await _sync_payment_if_needed(db, payment)
    return {
        "id": str(payment.id),
        "status": payment.status.value,
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_payment_id": payment.razorpay_payment_id,
    }


@router.post("/admin/payments/{payment_id}/refund")
async def admin_refund(
    payment_id: UUID,
    body: RefundBody,
    db: AsyncSession = Depends(get_db),
    admin: AdminUser = Depends(require_super_admin),
):
    if not body.reason:
        raise HTTPException(status_code=400, detail="reason is required")
    try:
        result = await refund_payment(db, payment_id, body.reason)
    except RefundError as err:
        raise HTTPException(status_code=err.status, detail=str(err))

    from app.services import audit_service

    await audit_service.log_activity(
        db,
        action="PAYMENT_REFUNDED",
        resource_type="PAYMENT",
        resource_id=payment_id,
        actor_user_id=admin.user_id,
        actor_role="SUPER_ADMIN",
        status="SUCCESS",
        details={
            "reason": body.reason,
            "refund_id": result.refund_id,
            "refund_amount_paise": result.refund_amount_paise,
        },
    )
    await db.commit()

    return {
        "paymentId": str(result.payment_id),
        "refundId": result.refund_id,
        "refundAmountPaise": result.refund_amount_paise,
    }
