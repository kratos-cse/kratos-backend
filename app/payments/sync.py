"""Razorpay order sync — CREATED in-flight checkout only (never repairs FAILED)."""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_success
from app.payments.razorpay_verify import find_verified_capture_for_payment
from app.payments.reconciliation import log_payment_reconciliation

logger = logging.getLogger("payments.sync")


async def _reload_payment(db: AsyncSession, payment_id: UUID) -> Payment:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise RuntimeError(f"payment {payment_id} missing after sync rollback")
    return payment


async def sync_payment_from_razorpay(db: AsyncSession, payment: Payment) -> Payment:
    """
    If Razorpay shows a verified captured payment for this CREATED order, run apply_payment_success.

    FAILED / PAID / REFUNDED rows are never modified by sync — use reconcile or webhooks instead.
    """
    if payment.status != PaymentStatus.CREATED:
        return payment

    if not (
        payment.razorpay_order_id
        and settings.RAZORPAY_KEY_ID
        and settings.RAZORPAY_KEY_SECRET
    ):
        return payment

    payment_id = payment.id
    log_payment_reconciliation("sync_start", payment=payment)

    try:
        verified = await find_verified_capture_for_payment(payment)
        if verified is None:
            return await _reload_payment(db, payment_id)
        applied = await apply_payment_success(db, payment_id, verified.razorpay_payment_id)
        if applied.applied:
            return await _reload_payment(db, payment_id)
        return await _reload_payment(db, payment_id)
    except Exception:
        logger.exception(
            "payment_sync_failed payment_id=%s razorpay_order_id=%s",
            payment_id,
            payment.razorpay_order_id,
        )
        await db.rollback()
        return await _reload_payment(db, payment_id)
