import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import notification_service, qr_service, receipt_service

logger = logging.getLogger("payments.handoffs")


async def trigger_qr(
    db: AsyncSession,
    registration_id: Optional[UUID] = None,
    team_member_id: Optional[UUID] = None,
) -> None:
    if registration_id is not None:
        await qr_service.generate_for_registration(db, registration_id)
    elif team_member_id is not None:
        await qr_service.generate_for_team_member(db, team_member_id)
    else:
        logger.warning("[handoff] trigger_qr called with no registration_id or team_member_id")


async def send_notification(
    db: AsyncSession,
    kind: str,
    payer_profile_id: UUID,
    amount_paise: int,
    reason: Optional[str] = None,
    *,
    payment_id: Optional[UUID] = None,
) -> None:
    """Legacy handoff entry — delegates to notification_service (never raises on SMTP failure)."""
    if kind in ("PAYMENT_CONFIRMED", "PAYMENT_CONFIRMATION") and payment_id is not None:
        await notification_service.notify_payment_confirmed(db, payment_id)
        return
    if kind in ("REFUND", "REFUND_ISSUED") and payment_id is not None:
        await notification_service.notify_refund(db, payment_id, reason=reason, amount_paise=amount_paise)
        return
    logger.info(
        "[handoff] send_notification unhandled kind=%s payer_profile_id=%s payment_id=%s",
        kind,
        payer_profile_id,
        payment_id,
    )


async def issue_receipt(db: AsyncSession, payment_id: UUID) -> None:
    logger.info("receipt_generation_started payment_id=%s", payment_id)
    try:
        await receipt_service.ensure_receipt(db, payment_id)
        logger.info("receipt_generation_succeeded payment_id=%s", payment_id)
    except Exception:
        logger.exception("receipt_generation_failed payment_id=%s", payment_id)
        raise
