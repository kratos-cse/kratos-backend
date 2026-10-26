"""Admin-recorded offline / manual payments (not Razorpay)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminUser
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.event import EventRegistrationRule
from app.models.payment import Payment
from app.models.registration import Registration
from app.payments.amounts import compute_amount_paise
from app.payments.apply import confirm_solo_registration, confirm_team_registration
from app.payments.fulfillment import run_post_payment_fulfillment
from app.services import audit_service
from app.services.event_service import enforce_capacity_before_confirmation

logger = logging.getLogger("payments.admin_offline")

AUDIT_ACTION = "ADMIN_OFFLINE_PAYMENT_CONFIRMED"
OFFLINE_ORDER_PREFIX = "admin_offline_"


@dataclass
class OfflineConfirmResult:
    registration_id: uuid.UUID
    payment_id: uuid.UUID
    registration_status: RegistrationStatus
    payment_status: PaymentStatus
    fulfillment: dict[str, bool]


def _is_razorpay_order_id(order_id: str) -> bool:
    return order_id.startswith("order_") or order_id.startswith("ord_")


async def _payment_type_for_registration(db: AsyncSession, registration: Registration) -> PaymentType:
    if registration.team_id is not None:
        return PaymentType.TEAM_REGISTRATION
    return PaymentType.SOLO_REGISTRATION


async def confirm_offline_payment_for_registration(
    db: AsyncSession,
    admin: AdminUser,
    *,
    registration_id: uuid.UUID,
    amount_paise: Optional[int] = None,
    payment_method: str,
    payment_reference: str,
    currency: str = "INR",
) -> OfflineConfirmResult:
    """
    Record an admin-verified offline payment and run confirmation + fulfillment.
    Does not create fake Razorpay payment ids — uses admin_offline_* order ids only.
    """
    if not payment_method.strip() or not payment_reference.strip():
        raise HTTPException(status_code=400, detail="payment_method and payment_reference are required")

    reg_result = await db.execute(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    registration = reg_result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=404, detail="Registration not found")
    if registration.status == RegistrationStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="Cannot confirm payment for a cancelled registration")
    if registration.status == RegistrationStatus.CONFIRMED and registration.payment_id:
        pay = (
            await db.execute(select(Payment).where(Payment.id == registration.payment_id))
        ).scalar_one_or_none()
        if pay and pay.status == PaymentStatus.PAID:
            from app.payments.fulfillment import retry_post_payment_fulfillment

            fulfillment = await retry_post_payment_fulfillment(db, pay.id)
            await db.commit()
            return OfflineConfirmResult(
                registration_id=registration.id,
                payment_id=pay.id,
                registration_status=registration.status,
                payment_status=pay.status,
                fulfillment=fulfillment,
            )

    rules = (
        await db.execute(
            select(EventRegistrationRule).where(EventRegistrationRule.event_id == registration.event_id)
        )
    ).scalar_one_or_none()
    if rules is None:
        raise HTTPException(status_code=404, detail="Event registration rules not found")

    if amount_paise is None:
        amount_paise = await compute_amount_paise(db, registration.event_id)

    payer_profile_id = registration.profile_id
    if payer_profile_id is None and registration.team_id is not None:
        from app.models.team import Team

        team = (
            await db.execute(select(Team).where(Team.id == registration.team_id))
        ).scalar_one_or_none()
        if team is None:
            raise HTTPException(status_code=409, detail="Team not found for registration")
        payer_profile_id = team.leader_profile_id

    if payer_profile_id is None:
        raise HTTPException(status_code=409, detail="Cannot determine payer for this registration")

    payment_type = await _payment_type_for_registration(db, registration)
    order_id = f"{OFFLINE_ORDER_PREFIX}{registration_id.hex}_{uuid.uuid4().hex[:12]}"

    payment = Payment(
        payer_profile_id=payer_profile_id,
        payment_type=payment_type,
        registration_id=registration.id,
        razorpay_order_id=order_id,
        razorpay_payment_id=None,
        amount_paise=amount_paise,
        currency=currency,
        status=PaymentStatus.PAID,
        provider_payment_method=payment_method.strip()[:64],
        provider_error_description=f"ADMIN_REF:{payment_reference.strip()[:500]}",
    )
    db.add(payment)
    await db.flush()

    registration.payment_id = payment.id
    await db.flush()

    await enforce_capacity_before_confirmation(db, registration)
    if payment_type == PaymentType.SOLO_REGISTRATION:
        await confirm_solo_registration(db, registration)
    else:
        await confirm_team_registration(db, registration, payer_profile_id)

    await db.commit()
    await db.refresh(payment)
    await db.refresh(registration)

    fulfillment = await run_post_payment_fulfillment(
        db, payment=payment, registration=registration, send_notification=True
    )
    await db.commit()

    await audit_service.log_activity(
        None,
        action=AUDIT_ACTION,
        resource_type="REGISTRATION",
        resource_id=registration.id,
        actor_user_id=admin.user_id,
        actor_role=(admin.role.name if getattr(admin, "role", None) else None) or "ADMIN",
        status="SUCCESS",
        details={
            "payment_id": str(payment.id),
            "amount_paise": amount_paise,
            "payment_method": payment_method,
            "payment_reference": payment_reference,
            "fulfillment": fulfillment,
        },
    )

    return OfflineConfirmResult(
        registration_id=registration.id,
        payment_id=payment.id,
        registration_status=registration.status,
        payment_status=payment.status,
        fulfillment=fulfillment,
    )


def assert_recoverable_razorpay_payment(payment: Payment) -> None:
    if payment.razorpay_order_id.startswith(OFFLINE_ORDER_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="This payment was recorded offline; use offline confirmation, not Razorpay recovery.",
        )
    if not _is_razorpay_order_id(payment.razorpay_order_id):
        raise HTTPException(
            status_code=400,
            detail="Payment order id is not a Razorpay order; cannot use captured-payment recovery.",
        )
