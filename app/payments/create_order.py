"""Create-order flow — idempotent active attempts and safe retry after failure."""
from __future__ import annotations

import uuid
from typing import Callable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PAYMENT_ALREADY_PROCESSED, AppError
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.event import EventRegistrationRule
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.team import Team
from app.payments.amounts import compute_amount_paise
from app.payments.razorpay_client import get_razorpay, with_retry_async

_BLOCK_NEW_ORDER = frozenset({PaymentStatus.PAID, PaymentStatus.REFUNDED})
SyncPaymentFn = Callable[[AsyncSession, Payment], Payment]


def order_response(payment: Payment) -> dict:
    return {
        "paymentId": str(payment.id),
        "razorpayOrderId": payment.razorpay_order_id,
        "amountPaise": payment.amount_paise,
        "currency": payment.currency,
        "razorpayKeyId": settings.RAZORPAY_KEY_ID,
    }


async def resolve_registration_for_create_order(
    db: AsyncSession,
    *,
    event_id: UUID,
    payer: Profile,
    payment_type: PaymentType,
    registration_id: Optional[UUID],
) -> Registration:
    if registration_id:
        result = await db.execute(
            select(Registration).where(Registration.id == registration_id).with_for_update()
        )
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="Registration not found")
    elif payment_type == PaymentType.SOLO_REGISTRATION:
        result = await db.execute(
            select(Registration)
            .where(
                Registration.event_id == event_id,
                Registration.profile_id == payer.id,
                Registration.status != RegistrationStatus.CANCELLED,
            )
            .with_for_update()
        )
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="No pending solo registration for this event")
    else:
        team_result = await db.execute(
            select(Team).where(Team.event_id == event_id, Team.leader_profile_id == payer.id)
        )
        team = team_result.scalar_one_or_none()
        if not team:
            raise HTTPException(status_code=404, detail="No team found for payer on this event")
        result = await db.execute(
            select(Registration)
            .where(
                Registration.team_id == team.id,
                Registration.status != RegistrationStatus.CANCELLED,
            )
            .with_for_update()
        )
        registration = result.scalar_one_or_none()
        if not registration:
            raise HTTPException(status_code=404, detail="No pending team registration for this event")

    if registration.event_id != event_id:
        raise HTTPException(status_code=400, detail="Registration does not belong to this event")
    if payment_type == PaymentType.TEAM_REGISTRATION and not registration.team_id:
        raise HTTPException(status_code=400, detail="Registration is not a team registration")
    if payment_type == PaymentType.SOLO_REGISTRATION and not registration.profile_id:
        raise HTTPException(status_code=400, detail="Registration is not a solo registration")
    if registration.status == RegistrationStatus.CONFIRMED:
        raise AppError(
            PAYMENT_ALREADY_PROCESSED,
            "This registration has already been paid",
            status_code=409,
        )

    return registration


async def existing_payment_order_response(
    db: AsyncSession,
    registration: Registration,
    *,
    sync_payment: SyncPaymentFn,
) -> Optional[dict]:
    """
    Reuse an in-flight CREATED payment order, or allow a new attempt after FAILED.

    Returns an order payload when the existing CREATED payment should be reused.
    Returns None when a new payment/order should be created.
    Raises when the registration is already paid/refunded.
    """
    if not registration.payment_id:
        return None

    result = await db.execute(select(Payment).where(Payment.id == registration.payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        return None

    payment = await sync_payment(db, payment)

    if payment.status in _BLOCK_NEW_ORDER:
        raise AppError(
            PAYMENT_ALREADY_PROCESSED,
            "This registration has already been paid",
            status_code=409,
        )
    if payment.status == PaymentStatus.CREATED:
        return order_response(payment)
    if payment.status == PaymentStatus.FAILED:
        return None

    return None


async def create_payment_order(
    db: AsyncSession,
    *,
    event_id: UUID,
    payment_type: PaymentType,
    payer: Profile,
    registration_id: Optional[UUID],
    sync_payment: SyncPaymentFn,
) -> dict:
    rules_result = await db.execute(
        select(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id)
    )
    if not rules_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"No fee rules for event {event_id}")

    registration = await resolve_registration_for_create_order(
        db,
        event_id=event_id,
        payer=payer,
        payment_type=payment_type,
        registration_id=registration_id,
    )

    reused = await existing_payment_order_response(db, registration, sync_payment=sync_payment)
    if reused is not None:
        await db.commit()
        return reused

    try:
        amount_paise = await compute_amount_paise(db, event_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))

    receipt = f"kratos26_{uuid.uuid4().hex[:16]}"
    order = await with_retry_async(
        lambda: get_razorpay().order.create(
            {
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": {
                    "payment_type": payment_type.value,
                    "event_id": str(event_id),
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

    registration.payment_id = payment.id
    await db.commit()
    await db.refresh(payment)
    return order_response(payment)
