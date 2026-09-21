"""Refund flow: Razorpay refund -> payments row -> linked entity state."""
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus, TeamStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import Team
from app.services import notification_service
from app.payments.razorpay_client import get_razorpay, with_retry


class RefundError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class RefundResult:
    payment_id: UUID
    refund_id: str
    refund_amount_paise: int


async def refund_payment(db: AsyncSession, payment_id: UUID, reason: str) -> RefundResult:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise RefundError(f"Payment {payment_id} not found", status=404)
    if payment.status != PaymentStatus.PAID:
        raise RefundError(f"Payment {payment_id} is not PAID (status={payment.status})", status=409)
    if not payment.razorpay_payment_id:
        raise RefundError(f"Payment {payment_id} has no razorpay_payment_id to refund", status=409)

    refund = with_retry(
        lambda: get_razorpay().payment.refund(
            payment.razorpay_payment_id, {"amount": payment.amount_paise}
        )
    )

    updated = await db.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PAID)
        .values(
            status=PaymentStatus.REFUNDED,
            refund_id=refund["id"],
            refund_amount_paise=int(refund["amount"]),
            refunded_at=datetime.now(timezone.utc),
            refund_reason=reason,
            updated_at=datetime.now(timezone.utc),
        )
        .returning(Payment)
    )
    if updated.scalar_one_or_none() is None:
        raise RefundError(
            f"Razorpay refund {refund['id']} succeeded but payments row update failed for {payment_id}",
            status=500,
        )

    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        await db.execute(
            update(Registration)
            .where(Registration.payment_id == payment.id)
            .values(status=RegistrationStatus.CANCELLED)
        )
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        reg = await db.execute(select(Registration).where(Registration.payment_id == payment.id))
        registration = reg.scalar_one_or_none()
        if registration and registration.team_id:
            await db.execute(
                update(Team).where(Team.id == registration.team_id).values(status=TeamStatus.CANCELLED)
            )
            await db.execute(
                update(Registration)
                .where(Registration.id == registration.id)
                .values(status=RegistrationStatus.CANCELLED)
            )

    await db.commit()

    await notification_service.notify_refund(
        db,
        payment.id,
        reason=reason,
        amount_paise=int(refund["amount"]),
    )

    return RefundResult(
        payment_id=payment.id,
        refund_id=refund["id"],
        refund_amount_paise=int(refund["amount"]),
    )
