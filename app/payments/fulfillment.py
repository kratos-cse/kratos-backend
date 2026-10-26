"""Post-payment fulfillment: QR, receipt, notifications (safe to retry)."""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentType, TeamMemberStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import TeamMember
from app.payments.handoffs import trigger_qr
from app.services import notification_service, receipt_service

logger = logging.getLogger("payments.fulfillment")


async def ensure_qr_for_registration(db: AsyncSession, registration: Registration) -> None:
    if registration.profile_id is not None:
        await trigger_qr(db, registration_id=registration.id)
        return
    if registration.team_id is not None:
        members_result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == registration.team_id,
                TeamMember.status.notin_(
                    (TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)
                ),
            )
        )
        for member in members_result.scalars().all():
            await trigger_qr(db, team_member_id=member.id)


async def ensure_receipt_safe(db: AsyncSession, payment_id: UUID) -> bool:
    try:
        await receipt_service.ensure_receipt(db, payment_id)
        return True
    except Exception:
        logger.exception("fulfillment_receipt_failed payment_id=%s", payment_id)
        return False


async def notify_payment_confirmed_safe(db: AsyncSession, payment_id: UUID) -> bool:
    try:
        await notification_service.notify_payment_confirmed(db, payment_id)
        return True
    except Exception:
        logger.exception("fulfillment_notification_failed payment_id=%s", payment_id)
        return False


async def run_post_payment_fulfillment(
    db: AsyncSession,
    *,
    payment: Payment,
    registration: Optional[Registration],
    send_notification: bool = True,
) -> dict[str, bool]:
    """
    Idempotent QR + receipt; notification failures do not roll back payment state.
    Caller must commit registration/payment changes before calling notify (post-commit).
    """
    qr_ok = False
    receipt_ok = False
    notify_ok = False

    if registration is not None:
        try:
            if payment.payment_type == PaymentType.SOLO_REGISTRATION:
                await ensure_qr_for_registration(db, registration)
                qr_ok = True
            elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
                await ensure_qr_for_registration(db, registration)
                qr_ok = True
        except Exception:
            logger.exception(
                "fulfillment_qr_failed payment_id=%s registration_id=%s",
                payment.id,
                registration.id,
            )

    receipt_ok = await ensure_receipt_safe(db, payment.id)

    if send_notification:
        notify_ok = await notify_payment_confirmed_safe(db, payment.id)
    else:
        notify_ok = True

    return {"qr": qr_ok, "receipt": receipt_ok, "notification": notify_ok}


async def retry_post_payment_fulfillment(db: AsyncSession, payment_id: UUID) -> dict[str, bool]:
    """Re-run QR/receipt/notification for an already PAID payment."""
    payment = (
        await db.execute(select(Payment).where(Payment.id == payment_id))
    ).scalar_one_or_none()
    if payment is None:
        return {"qr": False, "receipt": False, "notification": False}
    from app.payments.reconciliation import resolve_registration_for_payment

    registration = await resolve_registration_for_payment(db, payment)
    return await run_post_payment_fulfillment(
        db, payment=payment, registration=registration, send_notification=True
    )
