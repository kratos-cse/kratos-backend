"""Razorpay webhook event handling."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_failure, apply_payment_success
from app.payments.razorpay_verify import (
    find_verified_capture_for_payment,
    razorpay_payment_is_captured,
    verify_capture_by_payment_id,
)

logger = logging.getLogger("payments.webhook")


def _payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    return (payload.get("payment") or {}).get("entity") or {}


def _order_entity(payload: dict[str, Any]) -> dict[str, Any]:
    return (payload.get("order") or {}).get("entity") or {}


async def _load_payment_by_order_id(db: AsyncSession, order_id: str) -> Optional[Payment]:
    result = await db.execute(select(Payment).where(Payment.razorpay_order_id == order_id))
    return result.scalar_one_or_none()


def _failure_metadata(entity: dict[str, Any]) -> dict[str, Any]:
    error = entity.get("error") or {}
    if isinstance(error, str):
        error = {"description": error}
    return {
        "provider_error_code": error.get("code") or entity.get("error_code"),
        "provider_error_description": error.get("description") or entity.get("error_description"),
        "provider_error_source": error.get("source") or entity.get("error_source"),
        "provider_error_step": error.get("step") or entity.get("error_step"),
        "provider_payment_method": entity.get("method"),
        "last_provider_event_at": datetime.now(timezone.utc),
    }


async def handle_webhook_event(db: AsyncSession, event: dict[str, Any]) -> dict[str, Any]:
    event_type = event.get("event") or ""
    payload = event.get("payload") or {}

    if event_type == "payment.captured":
        entity = _payment_entity(payload)
        order_id = entity.get("order_id")
        if not order_id:
            return {"received": True, "applied": False, "reason": "missing order_id"}
        payment = await _load_payment_by_order_id(db, order_id)
        if payment is None:
            return {"received": True, "applied": False, "reason": "unknown order"}
        razorpay_payment_id = str(entity.get("id") or "")
        verified = await verify_capture_by_payment_id(payment, razorpay_payment_id)
        if verified is None and razorpay_payment_is_captured(entity):
            verified = await verify_capture_by_payment_id(payment, razorpay_payment_id)
        if verified is None:
            logger.warning(
                "webhook_capture_unverified payment_id=%s order_id=%s",
                payment.id,
                order_id,
            )
            return {"received": True, "applied": False, "reason": "capture_not_verified"}
        applied = await apply_payment_success(db, payment.id, verified.razorpay_payment_id)
        return {"received": True, "applied": applied.applied, "event": event_type}

    if event_type == "payment.failed":
        entity = _payment_entity(payload)
        order_id = entity.get("order_id")
        if not order_id:
            return {"received": True, "applied": False, "reason": "missing order_id"}
        payment = await _load_payment_by_order_id(db, order_id)
        if payment is None:
            return {"received": True, "applied": False, "reason": "unknown order"}
        if payment.status in (PaymentStatus.PAID, PaymentStatus.REFUNDED):
            return {"received": True, "applied": False, "reason": "already_paid"}
        meta = _failure_metadata(entity)
        applied = await apply_payment_failure(db, payment.id, **meta)
        return {"received": True, "applied": applied.applied, "event": event_type}

    if event_type == "order.paid":
        order = _order_entity(payload)
        order_id = order.get("id")
        if not order_id:
            return {"received": True, "applied": False, "reason": "missing order id"}
        payment = await _load_payment_by_order_id(db, str(order_id))
        if payment is None:
            return {"received": True, "applied": False, "reason": "unknown order"}
        verified = await find_verified_capture_for_payment(payment)
        if verified is None:
            return {"received": True, "applied": False, "reason": "no_verified_capture"}
        applied = await apply_payment_success(db, payment.id, verified.razorpay_payment_id)
        return {"received": True, "applied": applied.applied, "event": event_type}

    return {"received": True, "applied": False, "event": event_type or None}
