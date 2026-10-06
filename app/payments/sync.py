"""Razorpay order sync — explicit mutation path (never from GET registration)."""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_success
from app.payments.razorpay_client import get_razorpay, with_retry_async
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
    If Razorpay shows a captured payment for this order, run apply_payment_success.

    On failure, rolls back the session so subsequent ORM operations remain usable.
    """
    if not (
        payment.status == PaymentStatus.CREATED
        and payment.razorpay_order_id
        and settings.RAZORPAY_KEY_ID
        and settings.RAZORPAY_KEY_SECRET
    ):
        return payment

    payment_id = payment.id
    log_payment_reconciliation("sync_start", payment=payment)

    try:
        order_payments = await with_retry_async(
            lambda: get_razorpay().order.payments(payment.razorpay_order_id)
        )
        items = order_payments.get("items", [])
        for item in items:
            if item.get("status") not in ("captured", "authorized"):
                continue
            razorpay_payment_id = item["id"]
            applied = await apply_payment_success(db, payment_id, razorpay_payment_id)
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

    return await _reload_payment(db, payment_id)
