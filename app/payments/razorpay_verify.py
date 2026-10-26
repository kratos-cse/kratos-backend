"""Fetch and verify Razorpay payment state against local Payment rows."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.models.payment import Payment
from app.payments.razorpay_client import get_razorpay, with_retry_async

logger = logging.getLogger("payments.razorpay_verify")


def razorpay_payment_is_captured(item: dict[str, Any]) -> bool:
    return str(item.get("status") or "").lower() == "captured"


def _amount_matches(item: dict[str, Any], payment: Payment) -> bool:
    try:
        return int(item.get("amount", -1)) == int(payment.amount_paise)
    except (TypeError, ValueError):
        return False


def _currency_matches(item: dict[str, Any], payment: Payment) -> bool:
    item_currency = str(item.get("currency") or "INR").upper()
    local_currency = str(payment.currency or "INR").upper()
    return item_currency == local_currency


def _order_matches(item: dict[str, Any], payment: Payment) -> bool:
    return str(item.get("order_id") or "") == str(payment.razorpay_order_id or "")


def payment_item_matches_local(item: dict[str, Any], payment: Payment) -> bool:
    if not razorpay_payment_is_captured(item):
        return False
    return _order_matches(item, payment) and _amount_matches(item, payment) and _currency_matches(item, payment)


@dataclass
class VerifiedCapture:
    razorpay_payment_id: str
    amount_paise: int
    currency: str
    status: str


async def fetch_order_payment_items(razorpay_order_id: str) -> list[dict[str, Any]]:
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        return []
    try:
        order_payments = await with_retry_async(
            lambda: get_razorpay().order.payments(razorpay_order_id)
        )
        items = order_payments.get("items", [])
        return items if isinstance(items, list) else []
    except Exception:
        logger.exception("razorpay_fetch_order_payments_failed order_id=%s", razorpay_order_id)
        return []


async def find_verified_capture_for_payment(payment: Payment) -> Optional[VerifiedCapture]:
    """Return a captured Razorpay payment that matches this local order/amount/currency."""
    if not payment.razorpay_order_id:
        return None
    items = await fetch_order_payment_items(payment.razorpay_order_id)
    captured: list[VerifiedCapture] = []
    for item in items:
        if not payment_item_matches_local(item, payment):
            continue
        captured.append(
            VerifiedCapture(
                razorpay_payment_id=str(item["id"]),
                amount_paise=int(item.get("amount", 0)),
                currency=str(item.get("currency") or "INR"),
                status=str(item.get("status")),
            )
        )
    if not captured:
        return None
    if len(captured) > 1:
        logger.warning(
            "razorpay_multiple_captures_for_order payment_id=%s order_id=%s count=%s",
            payment.id,
            payment.razorpay_order_id,
            len(captured),
        )
    return captured[0]


async def fetch_payment_entity(razorpay_payment_id: str) -> Optional[dict[str, Any]]:
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        return None
    try:
        entity = await with_retry_async(lambda: get_razorpay().payment.fetch(razorpay_payment_id))
        return entity if isinstance(entity, dict) else None
    except Exception:
        logger.exception("razorpay_fetch_payment_failed razorpay_payment_id=%s", razorpay_payment_id)
        return None


async def verify_capture_by_payment_id(
    payment: Payment, razorpay_payment_id: str
) -> Optional[VerifiedCapture]:
    """Prove capture via Razorpay API — caller id is a lookup hint, not proof."""
    entity = await fetch_payment_entity(razorpay_payment_id)
    if entity is None:
        return None
    if not payment_item_matches_local(entity, payment):
        return None
    return VerifiedCapture(
        razorpay_payment_id=str(entity["id"]),
        amount_paise=int(entity.get("amount", 0)),
        currency=str(entity.get("currency") or "INR"),
        status=str(entity.get("status")),
    )
