"""Payment ↔ registration trace and explicit reconciliation (no guessing)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus, RegistrationStatus
from app.models.payment import Payment
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration

logger = logging.getLogger("payments.reconciliation")


class ReconcileOutcome(str, Enum):
    OK = "OK"
    APPLIED = "APPLIED"
    PAYMENT_NOT_FOUND = "PAYMENT_NOT_FOUND"
    REGISTRATION_UNLINKED = "REGISTRATION_UNLINKED"
    REGISTRATION_CANCELLED = "REGISTRATION_CANCELLED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_CAPTURED = "NOT_CAPTURED"
    DUPLICATE_CAPTURE = "DUPLICATE_CAPTURE"


@dataclass
class PaymentTrace:
    razorpay_order_id: Optional[str]
    razorpay_payment_id: Optional[str]
    payment_id: Optional[UUID]
    payment_status: Optional[str]
    registration_id: Optional[UUID]
    registration_status: Optional[str]
    team_id: Optional[UUID]
    qr_active: Optional[bool]
    receipt_exists: bool
    notes: list[str]


def log_payment_reconciliation(
    operation: str,
    *,
    payment: Optional[Payment] = None,
    registration: Optional[Registration] = None,
    payment_id: Optional[UUID] = None,
    registration_id: Optional[UUID] = None,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    payment_status: Optional[str] = None,
    registration_status: Optional[str] = None,
) -> None:
    logger.info(
        "payment_reconciliation operation=%s payment_id=%s registration_id=%s "
        "razorpay_order_id=%s razorpay_payment_id=%s payment_status=%s registration_status=%s",
        operation,
        payment.id if payment else payment_id,
        registration.id if registration else registration_id,
        payment.razorpay_order_id if payment else razorpay_order_id,
        payment.razorpay_payment_id if payment else razorpay_payment_id,
        payment.status.value if payment else payment_status,
        registration.status.value if registration else registration_status,
    )


async def registration_linked_to_payment(db: AsyncSession, payment_id: UUID) -> Optional[Registration]:
    result = await db.execute(select(Registration).where(Registration.payment_id == payment_id))
    return result.scalar_one_or_none()


async def resolve_registration_for_payment(db: AsyncSession, payment: Payment) -> Optional[Registration]:
    """Durable registration association — never infer from payer identity alone."""
    if payment.registration_id is not None:
        result = await db.execute(
            select(Registration).where(Registration.id == payment.registration_id)
        )
        registration = result.scalar_one_or_none()
        if registration is not None:
            return registration
    return await registration_linked_to_payment(db, payment.id)


async def other_paid_payment_for_registration(
    db: AsyncSession, registration_id: UUID, exclude_payment_id: UUID
) -> Optional[Payment]:
    result = await db.execute(
        select(Payment).where(
            Payment.registration_id == registration_id,
            Payment.id != exclude_payment_id,
            Payment.status == PaymentStatus.PAID,
        )
    )
    return result.scalar_one_or_none()


async def trace_payment(
    db: AsyncSession,
    *,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    payment_id: Optional[UUID] = None,
) -> PaymentTrace:
    notes: list[str] = []
    payment: Optional[Payment] = None

    if payment_id is not None:
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
    elif razorpay_order_id:
        result = await db.execute(select(Payment).where(Payment.razorpay_order_id == razorpay_order_id))
        payment = result.scalar_one_or_none()
    elif razorpay_payment_id:
        result = await db.execute(select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id))
        payment = result.scalar_one_or_none()

    if payment is None:
        return PaymentTrace(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            payment_id=payment_id,
            payment_status=None,
            registration_id=None,
            registration_status=None,
            team_id=None,
            qr_active=None,
            receipt_exists=False,
            notes=["internal payment row not found"],
        )

    registration = await resolve_registration_for_payment(db, payment)
    if registration is None:
        notes.append("no verifiable registration link (registration_id or registration.payment_id)")
    elif registration.status == RegistrationStatus.CANCELLED:
        notes.append("linked registration is CANCELLED (payment may be a late capture)")

    qr_active: Optional[bool] = None
    if registration is not None:
        if registration.profile_id is not None:
            qr_result = await db.execute(select(QRCode).where(QRCode.registration_id == registration.id))
            qr = qr_result.scalar_one_or_none()
            qr_active = qr.is_active if qr else None
        elif registration.team_id is not None:
            notes.append("team registration QR is per team member; check members separately")

    receipt_exists = (
        await db.execute(select(Receipt.id).where(Receipt.payment_id == payment.id))
    ).scalar_one_or_none() is not None

    log_payment_reconciliation(
        "trace",
        payment=payment,
        registration=registration,
    )

    return PaymentTrace(
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_payment_id=payment.razorpay_payment_id,
        payment_id=payment.id,
        payment_status=payment.status.value,
        registration_id=registration.id if registration else None,
        registration_status=registration.status.value if registration else None,
        team_id=registration.team_id if registration else None,
        qr_active=qr_active,
        receipt_exists=receipt_exists,
        notes=notes,
    )


def trace_to_dict(trace: PaymentTrace) -> dict[str, Any]:
    return {
        "razorpayOrderId": trace.razorpay_order_id,
        "razorpayPaymentId": trace.razorpay_payment_id,
        "paymentId": str(trace.payment_id) if trace.payment_id else None,
        "paymentStatus": trace.payment_status,
        "registrationId": str(trace.registration_id) if trace.registration_id else None,
        "registrationStatus": trace.registration_status,
        "teamId": str(trace.team_id) if trace.team_id else None,
        "qrActive": trace.qr_active,
        "receiptExists": trace.receipt_exists,
        "notes": trace.notes,
    }


@dataclass
class ReconcileResult:
    outcome: ReconcileOutcome
    trace: PaymentTrace
    applied: bool


async def reconcile_local_payment_state(
    db: AsyncSession,
    payment: Payment,
    *,
    razorpay_payment_id: Optional[str],
) -> ReconcileResult:
    """
    Apply idempotent post-payment effects when Razorpay has captured funds.

    Requires verified capture at Razorpay (not caller-supplied id alone).
    """
    from app.payments.apply import apply_payment_success
    from app.payments.razorpay_verify import (
        find_verified_capture_for_payment,
        verify_capture_by_payment_id,
    )

    trace = await trace_payment(db, payment_id=payment.id)
    registration = await resolve_registration_for_payment(db, payment)

    if registration is None:
        return ReconcileResult(
            outcome=ReconcileOutcome.REGISTRATION_UNLINKED,
            trace=trace,
            applied=False,
        )

    if registration.status == RegistrationStatus.CANCELLED:
        return ReconcileResult(
            outcome=ReconcileOutcome.REGISTRATION_CANCELLED,
            trace=trace,
            applied=False,
        )

    verified = None
    if razorpay_payment_id:
        verified = await verify_capture_by_payment_id(payment, razorpay_payment_id)
    if verified is None:
        verified = await find_verified_capture_for_payment(payment)
    if verified is None:
        return ReconcileResult(outcome=ReconcileOutcome.NOT_CAPTURED, trace=trace, applied=False)

    if payment.registration_id is not None:
        sibling = await other_paid_payment_for_registration(db, payment.registration_id, payment.id)
        if sibling is not None and payment.status != PaymentStatus.PAID:
            trace.notes.append("another PAID payment exists for the same registration")
            return ReconcileResult(
                outcome=ReconcileOutcome.DUPLICATE_CAPTURE,
                trace=trace,
                applied=False,
            )

    result = await apply_payment_success(
        db,
        payment.id,
        verified.razorpay_payment_id,
    )
    trace = await trace_payment(db, payment_id=payment.id)
    if result.applied:
        return ReconcileResult(outcome=ReconcileOutcome.APPLIED, trace=trace, applied=True)
    if trace.payment_status == PaymentStatus.PAID.value:
        return ReconcileResult(outcome=ReconcileOutcome.OK, trace=trace, applied=False)
    return ReconcileResult(outcome=ReconcileOutcome.AMBIGUOUS, trace=trace, applied=False)
