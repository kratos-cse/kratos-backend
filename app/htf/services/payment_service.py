"""HTF payment service.

Handles Razorpay order creation, payment verification, webhook ingestion,
idempotent status transition to PAID and application to CONFIRMED, and payment sync.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.htf.contracts import get_leader_profile_id, on_htf_confirmed
from app.htf.errors import (
    AMOUNT_MISMATCH,
    APPLICATION_NOT_FOUND,
    BAD_REQUEST,
    FORBIDDEN,
    GATEWAY_ERROR,
    INVALID_FEE,
    INVALID_JSON,
    INVALID_PAYMENT_SIGNATURE,
    INVALID_SIGNATURE,
    MISSING_SIGNATURE,
    NOT_SHORTLISTED,
    NOT_TEAM_LEADER,
    PAYMENT_ALREADY_COMPLETED,
    PAYMENT_DEADLINE_PASSED,
    PAYMENT_GATEWAY_NOT_CONFIGURED,
    PAYMENT_NOT_ELIGIBLE,
    PAYMENT_NOT_FOUND,
    WEBHOOK_SECRET_NOT_CONFIGURED,
    htf_error,
)
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.payment import HTFPayment
from app.htf.schemas.payment import PaymentOrderOut
from app.htf.settings import htf_settings
from app.models.profile import Profile
from app.payments.razorpay_client import (
    get_razorpay,
    razorpay_unavailable_message,
    with_retry_async,
)
from app.payments.signatures import (
    verify_checkout_signature,
    verify_webhook_signature,
)
from app.services.audit_service import log_activity

logger = logging.getLogger("htf.payments")

# Outcomes of confirm_htf_payment.
CONFIRMED = "confirmed"
ORPHANED = "orphaned"
ALREADY_APPLIED = "already_applied"

_OUTCOME_MESSAGES = {
    CONFIRMED: "Payment verified and application confirmed",
    ALREADY_APPLIED: "Payment was already processed",
    ORPHANED: (
        "Payment was received but this application is not eligible for confirmation. "
        "Please contact the organizers."
    ),
}


def _order_out(payment: HTFPayment) -> PaymentOrderOut:
    return PaymentOrderOut(
        paymentId=str(payment.id),
        razorpayOrderId=payment.razorpay_order_id,
        amountPaise=payment.amount_paise,
        currency=payment.currency,
        razorpayKeyId=settings.RAZORPAY_KEY_ID,
    )


async def create_payment_order(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    profile: Profile,
) -> PaymentOrderOut:
    now = datetime.now(timezone.utc)

    # 1. Lock application row
    app_query = await db.execute(
        select(HTFApplication).where(HTFApplication.id == application_id).with_for_update()
    )
    application = app_query.scalar_one_or_none()
    if not application:
        raise htf_error(APPLICATION_NOT_FOUND, "HTF application not found", status_code=404)

    # 2. Verify caller is team leader
    leader_id = await get_leader_profile_id(db, application.team_id)
    if leader_id != profile.id:
        raise htf_error(NOT_TEAM_LEADER, "Only the team leader may initiate payment", status_code=403)

    # 3. Guards. CONFIRMED is checked first so it gets its own error code.
    if application.status == "CONFIRMED":
        raise htf_error(PAYMENT_ALREADY_COMPLETED, "Application payment has already been confirmed", status_code=400)
    if application.status == "NOT_SHORTLISTED":
        raise htf_error(NOT_SHORTLISTED, "Application was not shortlisted for payment", status_code=403)
    if application.status != "SHORTLISTED":
        raise htf_error(PAYMENT_NOT_ELIGIBLE, f"Application in {application.status} status is not eligible for payment", status_code=403)

    # Check for existing PAID row
    paid_query = await db.execute(
        select(HTFPayment).where(
            HTFPayment.application_id == application_id,
            HTFPayment.status == "PAID",
        )
    )
    if paid_query.scalar_one_or_none() is not None:
        raise htf_error(PAYMENT_ALREADY_COMPLETED, "Payment has already been completed", status_code=400)

    # 4. Check payment window
    config_query = await db.execute(
        select(HTFEventConfig).where(HTFEventConfig.event_id == application.event_id)
    )
    config = config_query.scalar_one_or_none()
    if config:
        if config.payment_opens_at and now < config.payment_opens_at:
            raise htf_error(PAYMENT_DEADLINE_PASSED, "Payment window has not opened yet", status_code=403)
        if config.payment_closes_at and now > config.payment_closes_at:
            raise htf_error(PAYMENT_DEADLINE_PASSED, "Payment window deadline has passed", status_code=403)
        amount_paise = config.payment_amount_paise
        currency = config.currency or "INR"
    else:
        amount_paise = 0
        currency = "INR"

    if amount_paise <= 0:
        raise htf_error(INVALID_FEE, "Payment amount not properly configured for this event", status_code=400)

    # 5. Idempotency: Reuse existing CREATED order
    existing_order_query = await db.execute(
        select(HTFPayment).where(
            HTFPayment.application_id == application_id,
            HTFPayment.status == "CREATED",
        )
    )
    existing_payment = existing_order_query.scalar_one_or_none()
    if existing_payment:
        return _order_out(existing_payment)

    # 6. Create Razorpay order
    receipt = f"htf26_{uuid.uuid4().hex[:16]}"
    try:
        order = await with_retry_async(
            lambda: get_razorpay().order.create(
                {
                    "amount": amount_paise,
                    "currency": currency,
                    "receipt": receipt,
                    "notes": {
                        "domain": "HTF",
                        "application_id": str(application_id),
                        "team_id": str(application.team_id),
                    },
                }
            )
        )
    except requests.RequestException:
        raise htf_error(GATEWAY_ERROR, razorpay_unavailable_message(), status_code=503)
    except Exception as err:
        from razorpay.errors import BadRequestError, GatewayError, ServerError

        if isinstance(err, (ServerError, GatewayError)):
            raise htf_error(GATEWAY_ERROR, razorpay_unavailable_message(), status_code=503)
        if isinstance(err, BadRequestError):
            raise htf_error(BAD_REQUEST, "Could not create payment order with gateway", status_code=400)
        raise

    payment = HTFPayment(
        id=uuid.uuid4(),
        application_id=application_id,
        payer_profile_id=profile.id,
        razorpay_order_id=order["id"],
        amount_paise=amount_paise,
        currency=currency,
        status="CREATED",
        created_at=now,
        updated_at=now,
    )
    db.add(payment)
    await log_activity(
        db,
        action="HTF_PAYMENT_CREATED",
        resource_type="HTF_PAYMENT",
        resource_id=payment.id,
        actor_profile_id=profile.id,
        details={
            "application_id": str(application_id),
            "razorpay_order_id": order["id"],
            "amount_paise": amount_paise,
        },
    )
    await db.commit()
    await db.refresh(payment)

    return _order_out(payment)


async def _audit_orphan(
    db: AsyncSession,
    *,
    payment_id: uuid.UUID,
    application_id: uuid.UUID,
    razorpay_payment_id: str,
    reason: str,
) -> None:
    await log_activity(
        db,
        action="HTF_PAYMENT_ORPHANED",
        resource_type="HTF_PAYMENT",
        resource_id=payment_id,
        details={
            "application_id": str(application_id),
            "razorpay_payment_id": razorpay_payment_id,
            "reason": reason,
        },
    )
    await db.commit()


async def confirm_htf_payment(
    db: AsyncSession,
    *,
    htf_payment_id: uuid.UUID,
    razorpay_payment_id: str,
) -> str:
    """Shared single success path for payment verification, webhooks and sync.

    Returns one of CONFIRMED, ORPHANED (money captured but the application could not be
    confirmed; needs a manual refund) or ALREADY_APPLIED (nothing to do).
    """
    now = datetime.now(timezone.utc)

    # Column-only select: locks the row without caching a stale entity in the session.
    current_row = (
        await db.execute(
            select(HTFPayment.application_id, HTFPayment.status)
            .where(HTFPayment.id == htf_payment_id)
            .with_for_update()
        )
    ).first()
    if current_row is None or current_row.status not in ("CREATED", "FAILED"):
        return ALREADY_APPLIED
    application_id = current_row.application_id

    if current_row.status == "FAILED":
        # A capture can land on an order we had marked FAILED (checkout retry, late webhook)
        # while a newer order now exists. Only one live (CREATED/PAID) row is allowed per
        # application, so settle the newer order first.
        already_paid = await db.scalar(
            select(HTFPayment.id).where(
                HTFPayment.application_id == application_id,
                HTFPayment.id != htf_payment_id,
                HTFPayment.status == "PAID",
            )
        )
        if already_paid is not None:
            await _audit_orphan(
                db,
                payment_id=htf_payment_id,
                application_id=application_id,
                razorpay_payment_id=razorpay_payment_id,
                reason="Duplicate capture: the application already has a PAID payment",
            )
            return ORPHANED
        await db.execute(
            update(HTFPayment)
            .where(
                HTFPayment.application_id == application_id,
                HTFPayment.id != htf_payment_id,
                HTFPayment.status == "CREATED",
            )
            .values(status="FAILED", updated_at=now)
        )

    pay_update = await db.execute(
        update(HTFPayment)
        .where(
            HTFPayment.id == htf_payment_id,
            HTFPayment.status.in_(["CREATED", "FAILED"]),
        )
        .values(
            status="PAID",
            razorpay_payment_id=razorpay_payment_id,
            updated_at=now,
        )
        .returning(HTFPayment)
    )
    payment = pay_update.scalar_one_or_none()
    if not payment:
        return ALREADY_APPLIED

    app_update = await db.execute(
        update(HTFApplication)
        .where(
            HTFApplication.id == application_id,
            HTFApplication.status == "SHORTLISTED",
        )
        .values(status="CONFIRMED", updated_at=now)
        .returning(HTFApplication)
    )
    if not app_update.scalar_one_or_none():
        # Application was not SHORTLISTED (e.g. decision revoked). Payment stays PAID but the
        # application is never silently confirmed.
        await _audit_orphan(
            db,
            payment_id=payment.id,
            application_id=application_id,
            razorpay_payment_id=razorpay_payment_id,
            reason="Application was not in SHORTLISTED state at confirmation time",
        )
        return ORPHANED

    await log_activity(
        db,
        action="HTF_PAYMENT_CONFIRMED",
        resource_type="HTF_PAYMENT",
        resource_id=payment.id,
        actor_profile_id=payment.payer_profile_id,
        details={
            "application_id": str(application_id),
            "razorpay_payment_id": razorpay_payment_id,
            "amount_paise": payment.amount_paise,
        },
    )
    await log_activity(
        db,
        action="HTF_APPLICATION_CONFIRMED",
        resource_type="HTF_APPLICATION",
        resource_id=application_id,
        actor_profile_id=payment.payer_profile_id,
        details={"payment_id": str(payment.id)},
    )

    await db.commit()

    try:
        await on_htf_confirmed(application_id)
    except Exception as hook_exc:
        logger.warning("on_htf_confirmed hook failed for application %s: %s", application_id, hook_exc)

    return CONFIRMED


async def verify_payment(
    db: AsyncSession,
    *,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
    profile: Profile,
) -> dict[str, str]:
    # 1. Fail closed on empty secrets or signatures
    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_secret:
        raise htf_error(PAYMENT_GATEWAY_NOT_CONFIGURED, "Payment gateway secret not configured", status_code=503)
    if not razorpay_signature or not razorpay_order_id or not razorpay_payment_id:
        raise htf_error(INVALID_SIGNATURE, "Missing payment verification parameters", status_code=400)

    # 2. Look up payment by order id
    pay_query = await db.execute(
        select(HTFPayment).where(HTFPayment.razorpay_order_id == razorpay_order_id)
    )
    payment = pay_query.scalar_one_or_none()
    if not payment:
        raise htf_error(PAYMENT_NOT_FOUND, "HTF payment order not found", status_code=404)

    # 3. Caller must be payer
    if payment.payer_profile_id != profile.id:
        raise htf_error(FORBIDDEN, "Only the payer may verify this payment", status_code=403)

    # 4. Verify signature
    valid = verify_checkout_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature, key_secret)
    if not valid:
        raise htf_error(INVALID_PAYMENT_SIGNATURE, "Razorpay checkout signature verification failed", status_code=400)

    # 5. Confirm payment and report what actually happened
    outcome = await confirm_htf_payment(db, htf_payment_id=payment.id, razorpay_payment_id=razorpay_payment_id)
    return {
        "status": "orphaned" if outcome == ORPHANED else "success",
        "outcome": outcome,
        "message": _OUTCOME_MESSAGES[outcome],
    }


async def handle_webhook(
    db: AsyncSession,
    *,
    raw_body: bytes,
    signature: Optional[str],
) -> dict[str, str]:
    # 1. Fail closed on empty webhook secret or signature
    webhook_secret = htf_settings.HTF_RAZORPAY_WEBHOOK_SECRET
    if not webhook_secret:
        raise htf_error(WEBHOOK_SECRET_NOT_CONFIGURED, "HTF Razorpay webhook secret not configured", status_code=503)
    if not signature:
        raise htf_error(MISSING_SIGNATURE, "X-Razorpay-Signature header missing", status_code=400)

    # 2. Verify webhook signature
    valid = verify_webhook_signature(raw_body, signature, webhook_secret)
    if not valid:
        raise htf_error(INVALID_SIGNATURE, "Invalid webhook signature", status_code=400)

    # 3. Parse JSON safely
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise htf_error(INVALID_JSON, "Malformed JSON body in webhook", status_code=400)

    event_name = payload.get("event")
    payment_entity = (
        payload.get("payload", {})
        .get("payment", {})
        .get("entity", {})
    )
    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    amount = payment_entity.get("amount")

    if not order_id:
        return {"status": "ok", "reason": "No order_id in event"}

    # 4. Look up HTF payment
    pay_query = await db.execute(
        select(HTFPayment).where(HTFPayment.razorpay_order_id == order_id)
    )
    payment = pay_query.scalar_one_or_none()
    if not payment:
        # Unknown order: ignore cleanly, as this may be a regular KRATOS order
        return {"status": "ok", "reason": "unknown order"}

    # 5. The reported amount must be present and match the order
    if amount != payment.amount_paise:
        logger.error(
            "HTF Webhook: Amount mismatch for order %s. Expected %s, got %s",
            order_id,
            payment.amount_paise,
            amount,
        )
        raise htf_error(AMOUNT_MISMATCH, "Webhook payment amount does not match order amount", status_code=400)

    # 6. Process event types
    if event_name == "payment.captured" and payment_id:
        await confirm_htf_payment(db, htf_payment_id=payment.id, razorpay_payment_id=payment_id)
    elif event_name == "payment.failed":
        now = datetime.now(timezone.utc)
        await db.execute(
            update(HTFPayment)
            .where(
                HTFPayment.id == payment.id,
                HTFPayment.status == "CREATED",
            )
            .values(status="FAILED", updated_at=now)
        )
        await db.commit()

    return {"status": "ok"}


async def sync_payment(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    profile: Profile,
) -> dict[str, str]:
    """P1 sync: Polls Razorpay for the application's order if webhook/verify was missed."""
    from razorpay.errors import BadRequestError, GatewayError, ServerError

    app_query = await db.execute(
        select(HTFApplication).where(HTFApplication.id == application_id)
    )
    application = app_query.scalar_one_or_none()
    if not application:
        raise htf_error(APPLICATION_NOT_FOUND, "HTF application not found", status_code=404)

    leader_id = await get_leader_profile_id(db, application.team_id)
    if leader_id != profile.id:
        raise htf_error(NOT_TEAM_LEADER, "Only the team leader may sync payment", status_code=403)

    # Several rows can match once an order has failed and been re-created; take the newest.
    pay_query = await db.execute(
        select(HTFPayment)
        .where(
            HTFPayment.application_id == application_id,
            HTFPayment.status.in_(["CREATED", "FAILED"]),
        )
        .order_by(HTFPayment.created_at.desc())
        .limit(1)
    )
    payment = pay_query.scalars().first()
    if not payment:
        return {"status": "noop", "message": "No active order to sync"}

    order_id = payment.razorpay_order_id
    try:
        payments_data = await with_retry_async(lambda: get_razorpay().order.payments(order_id))
    except (requests.RequestException, BadRequestError, GatewayError, ServerError) as exc:
        logger.warning("Payment sync failed for order %s: %s", order_id, exc)
        return {"status": "gateway_unavailable", "message": razorpay_unavailable_message()}

    captured_id = next(
        (item.get("id") for item in payments_data.get("items", []) if item.get("status") == "captured"),
        None,
    )
    if not captured_id:
        return {"status": "unpaid", "message": "No captured payment found on gateway"}

    outcome = await confirm_htf_payment(db, htf_payment_id=payment.id, razorpay_payment_id=captured_id)
    return {
        "status": "synced" if outcome == CONFIRMED else outcome,
        "message": _OUTCOME_MESSAGES[outcome],
    }
