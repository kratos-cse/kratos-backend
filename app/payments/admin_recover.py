"""SUPER ADMIN — recover already-captured Razorpay payments for a known registration link."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import AdminUser
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.registration import Registration
from app.models.enums import TeamMemberStatus
from app.models.team import TeamMember
from app.payments.apply import apply_payment_success
from app.payments.razorpay_client import get_razorpay, with_retry_async
from app.payments.reconciliation import log_payment_reconciliation
from app.services import audit_service

logger = logging.getLogger("payments.admin_recover")

AUDIT_ACTION = "PAYMENT_RECOVERY_RECOVER_CAPTURED"


class PaymentRecoveryError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "PAYMENT_RECOVERY_FAILED"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


@dataclass
class RazorpayCaptureInfo:
    razorpay_payment_id: str
    amount_paise: int
    status: str


@dataclass
class RecoveryPreview:
    eligible: bool
    reason: Optional[str]
    participant_name: Optional[str]
    registration_id: UUID
    payment_id: UUID
    razorpay_order_id: Optional[str]
    razorpay_payment_id: Optional[str]
    amount_paise: int
    payment_status: str
    registration_status: str
    razorpay_capture_status: Optional[str]


@dataclass
class RecoveryResult:
    message: str
    payment_status: PaymentStatus
    registration_status: RegistrationStatus
    qr_active: bool
    receipt_generated: bool
    applied: bool


async def _load_registration(db: AsyncSession, registration_id: UUID) -> Registration:
    result = await db.execute(
        select(Registration)
        .options(
            selectinload(Registration.team),
            selectinload(Registration.payment),
        )
        .where(Registration.id == registration_id)
        .with_for_update()
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise PaymentRecoveryError("Registration not found.", status_code=404, code="NOT_FOUND")
    return registration


async def _load_payment(db: AsyncSession, payment_id: UUID) -> Payment:
    result = await db.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
    payment = result.scalar_one_or_none()
    if payment is None:
        raise PaymentRecoveryError("Payment not found.", status_code=404, code="NOT_FOUND")
    return payment


async def _assert_payment_registration_link(
    db: AsyncSession, registration: Registration, payment: Payment
) -> None:
    if registration.payment_id is None:
        raise PaymentRecoveryError(
            "Payment is not linked to this registration (registration.payment_id is empty).",
            status_code=409,
            code="PAYMENT_REGISTRATION_UNLINKED",
        )
    if registration.payment_id != payment.id:
        raise PaymentRecoveryError(
            "Payment does not belong to the selected registration.",
            status_code=409,
            code="PAYMENT_REGISTRATION_MISMATCH",
        )

    dup_count = await db.scalar(
        select(func.count())
        .select_from(Registration)
        .where(Registration.payment_id == payment.id)
    )
    if dup_count and dup_count > 1:
        raise PaymentRecoveryError(
            "Multiple registrations reference this payment; recovery is ambiguous.",
            status_code=409,
            code="PAYMENT_REGISTRATION_AMBIGUOUS",
        )


def _assert_payer_matches_registration(registration: Registration, payment: Payment) -> None:
    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        if registration.profile_id is None or registration.profile_id != payment.payer_profile_id:
            raise PaymentRecoveryError(
                "Payment payer does not match this solo registration.",
                status_code=409,
                code="PAYMENT_REGISTRATION_MISMATCH",
            )
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        if registration.team_id is None:
            raise PaymentRecoveryError("Registration is not a team registration.", status_code=400)
        if registration.team is None or registration.team.leader_profile_id != payment.payer_profile_id:
            raise PaymentRecoveryError(
                "Payment payer is not the team leader for this registration.",
                status_code=409,
                code="PAYMENT_REGISTRATION_MISMATCH",
            )
    else:
        raise PaymentRecoveryError(
            f"Unsupported payment type for recovery: {payment.payment_type}",
            status_code=400,
        )


async def fetch_razorpay_capture_for_order(razorpay_order_id: str) -> Optional[RazorpayCaptureInfo]:
    order_payments = await with_retry_async(lambda: get_razorpay().order.payments(razorpay_order_id))
    for item in order_payments.get("items", []):
        if item.get("status") in ("captured", "authorized"):
            return RazorpayCaptureInfo(
                razorpay_payment_id=str(item["id"]),
                amount_paise=int(item.get("amount", 0)),
                status=str(item.get("status")),
            )
    return None


async def _load_registration_read(db: AsyncSession, registration_id: UUID) -> Registration:
    result = await db.execute(
        select(Registration)
        .options(selectinload(Registration.team), selectinload(Registration.payment))
        .where(Registration.id == registration_id)
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise PaymentRecoveryError("Registration not found.", status_code=404, code="NOT_FOUND")
    return registration


async def _load_payment_read(db: AsyncSession, payment_id: UUID) -> Payment:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise PaymentRecoveryError("Payment not found.", status_code=404, code="NOT_FOUND")
    return payment


async def preview_recover_captured_payment(
    db: AsyncSession,
    *,
    registration_id: UUID,
    payment_id: UUID,
) -> RecoveryPreview:
    registration = await _load_registration_read(db, registration_id)
    payment = await _load_payment_read(db, payment_id)

    participant_name: Optional[str] = None
    if registration.team:
        participant_name = registration.team.name
    elif registration.profile_id:
        participant_name = str(registration.profile_id)

    reason: Optional[str] = None
    eligible = True

    if registration.status == RegistrationStatus.CANCELLED:
        eligible = False
        reason = (
            "Captured payment belongs to a cancelled registration and requires manual reconciliation."
        )
    else:
        try:
            await _assert_payment_registration_link(db, registration, payment)
            _assert_payer_matches_registration(registration, payment)
        except PaymentRecoveryError as err:
            eligible = False
            reason = err.message
    if payment.status == PaymentStatus.REFUNDED:
        eligible = False
        reason = "Payment has been refunded and cannot be recovered."

    capture: Optional[RazorpayCaptureInfo] = None
    capture_status: Optional[str] = None
    if payment.razorpay_order_id and eligible:
        try:
            capture = await fetch_razorpay_capture_for_order(payment.razorpay_order_id)
            capture_status = capture.status if capture else "not_captured"
            if capture is None:
                eligible = False
                reason = "Razorpay does not show a captured payment for this order."
            elif capture.amount_paise != payment.amount_paise:
                eligible = False
                reason = "Captured amount does not match the internal payment amount."
        except Exception:
            logger.exception("razorpay_preview_failed payment_id=%s", payment.id)
            eligible = False
            reason = "Could not verify capture status with Razorpay."

    return RecoveryPreview(
        eligible=eligible,
        reason=reason,
        participant_name=participant_name,
        registration_id=registration.id,
        payment_id=payment.id,
        razorpay_order_id=payment.razorpay_order_id,
        razorpay_payment_id=(capture.razorpay_payment_id if capture else payment.razorpay_payment_id),
        amount_paise=payment.amount_paise,
        payment_status=payment.status.value,
        registration_status=registration.status.value,
        razorpay_capture_status=capture_status,
    )


async def _audit_recovery(
    admin: AdminUser,
    *,
    payment: Payment,
    registration: Registration,
    outcome: str,
    previous_payment_status: str,
    previous_registration_status: str,
    razorpay_payment_id: Optional[str],
    failure_reason: Optional[str] = None,
) -> None:
    details: dict[str, Any] = {
        "payment_id": str(payment.id),
        "registration_id": str(registration.id),
        "razorpay_order_id": payment.razorpay_order_id,
        "razorpay_payment_id": razorpay_payment_id or payment.razorpay_payment_id,
        "previous_payment_status": previous_payment_status,
        "previous_registration_status": previous_registration_status,
        "outcome": outcome,
    }
    if failure_reason:
        details["failure_reason"] = failure_reason

    await audit_service.log_activity(
        None,
        action=AUDIT_ACTION,
        resource_type="PAYMENT",
        resource_id=payment.id,
        actor_user_id=admin.user_id,
        actor_role="SUPER_ADMIN",
        status="SUCCESS" if outcome == "SUCCESS" else "FAILURE",
        details=details,
    )


async def recover_captured_payment(
    db: AsyncSession,
    admin: AdminUser,
    *,
    registration_id: UUID,
    payment_id: UUID,
) -> RecoveryResult:
    registration = await _load_registration(db, registration_id)
    payment = await _load_payment(db, payment_id)
    prev_pay = payment.status.value
    prev_reg = registration.status.value

    log_payment_reconciliation(
        "admin_recover_start",
        payment=payment,
        registration=registration,
    )

    try:
        if registration.status == RegistrationStatus.CANCELLED:
            raise PaymentRecoveryError(
                "Captured payment belongs to a cancelled registration and requires manual reconciliation.",
                status_code=409,
                code="REGISTRATION_CANCELLED",
            )

        await _assert_payment_registration_link(db, registration, payment)
        _assert_payer_matches_registration(registration, payment)

        if payment.status == PaymentStatus.REFUNDED:
            raise PaymentRecoveryError("Payment has been refunded.", status_code=409)

        if not payment.razorpay_order_id:
            raise PaymentRecoveryError("Payment has no Razorpay order id.", status_code=409)

        capture = await fetch_razorpay_capture_for_order(payment.razorpay_order_id)
        if capture is None:
            raise PaymentRecoveryError(
                "Razorpay payment is not captured for this order.",
                status_code=409,
                code="RAZORPAY_NOT_CAPTURED",
            )
        if capture.amount_paise != payment.amount_paise:
            raise PaymentRecoveryError(
                "Captured amount does not match the internal payment amount.",
                status_code=409,
                code="AMOUNT_MISMATCH",
            )

        apply_result = await apply_payment_success(db, payment.id, capture.razorpay_payment_id)

        registration = await _load_registration_read(db, registration_id)
        payment = await _load_payment_read(db, payment_id)

        qr_active = False
        if registration.profile_id:
            qr = (
                await db.execute(
                    select(QRCode).where(
                        QRCode.registration_id == registration.id,
                        QRCode.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            qr_active = qr is not None
        elif registration.team_id:
            member_ids = (
                await db.execute(
                    select(TeamMember.id).where(
                        TeamMember.team_id == registration.team_id,
                        TeamMember.status == TeamMemberStatus.ACTIVE,
                    )
                )
            ).scalars().all()
            if member_ids:
                qr_count = await db.scalar(
                    select(func.count())
                    .select_from(QRCode)
                    .where(QRCode.team_member_id.in_(member_ids), QRCode.is_active.is_(True))
                )
                qr_active = bool(member_ids) and qr_count == len(member_ids)

        receipt_generated = (
            await db.execute(select(Receipt.id).where(Receipt.payment_id == payment.id))
        ).scalar_one_or_none() is not None

        if payment.status != PaymentStatus.PAID or registration.status != RegistrationStatus.CONFIRMED:
            raise PaymentRecoveryError(
                "Recovery did not reach PAID + CONFIRMED state.",
                status_code=500,
                code="RECOVERY_INCOMPLETE",
            )
        if not qr_active or not receipt_generated:
            raise PaymentRecoveryError(
                "Recovery incomplete: QR or receipt missing after payment success.",
                status_code=500,
                code="RECOVERY_INCOMPLETE",
            )

        await _audit_recovery(
            admin,
            payment=payment,
            registration=registration,
            outcome="SUCCESS",
            previous_payment_status=prev_pay,
            previous_registration_status=prev_reg,
            razorpay_payment_id=capture.razorpay_payment_id,
        )

        return RecoveryResult(
            message="Payment recovered successfully",
            payment_status=payment.status,
            registration_status=registration.status,
            qr_active=qr_active,
            receipt_generated=receipt_generated,
            applied=apply_result.applied,
        )
    except PaymentRecoveryError as err:
        await _audit_recovery(
            admin,
            payment=payment,
            registration=registration,
            outcome="FAILURE",
            previous_payment_status=prev_pay,
            previous_registration_status=prev_reg,
            razorpay_payment_id=payment.razorpay_payment_id,
            failure_reason=err.message,
        )
        raise
    except HTTPException:
        raise
    except Exception as err:
        logger.exception("admin_recover_unexpected payment_id=%s registration_id=%s", payment_id, registration_id)
        await _audit_recovery(
            admin,
            payment=payment,
            registration=registration,
            outcome="FAILURE",
            previous_payment_status=prev_pay,
            previous_registration_status=prev_reg,
            razorpay_payment_id=payment.razorpay_payment_id,
            failure_reason=str(err),
        )
        raise PaymentRecoveryError(
            "Payment recovery failed due to an internal error.",
            status_code=500,
        ) from err
