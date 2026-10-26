"""Shared email content helpers (escaping, links, event formatting)."""
from __future__ import annotations

import html
from typing import Optional
from urllib.parse import urlparse

from app.models.event import Event
from app.models.enums import PaymentStatus
from app.models.payment import Payment

_WHATSAPP_HOSTS = frozenset(
    {
        "chat.whatsapp.com",
        "wa.me",
        "www.whatsapp.com",
        "api.whatsapp.com",
    }
)


def sanitize_whatsapp_group_url(raw: Optional[str]) -> Optional[str]:
    """Return HTTPS WhatsApp invite URL or None if missing/invalid."""
    if not raw:
        return None
    url = raw.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    host = parsed.netloc.lower().split(":")[0]
    if host not in _WHATSAPP_HOSTS:
        return None
    return url


def whatsapp_cta_html(url: str, *, label: str = "Join the Event WhatsApp Group") -> str:
    safe_url = html.escape(url, quote=True)
    safe_label = html.escape(label)
    return (
        '<p style="margin:20px 0 0;text-align:center;">'
        f'<a href="{safe_url}" style="display:inline-block;background:#25D366;color:#fff;'
        'text-decoration:none;padding:12px 20px;border-radius:8px;font-size:14px;font-weight:600;">'
        f"{safe_label}</a></p>"
    )


def format_event_schedule(event: Event) -> tuple[str, str, Optional[str]]:
    """Date, start time, optional end time string."""
    date_str = "—"
    start_str = "—"
    end_str: Optional[str] = None
    if event.starts_at:
        date_str = event.starts_at.strftime("%d %b %Y")
        start_str = event.starts_at.strftime("%I:%M %p").lstrip("0")
    if event.ends_at:
        end_str = event.ends_at.strftime("%I:%M %p").lstrip("0")
    return date_str, start_str, end_str


def event_category_label(event: Event) -> Optional[str]:
    if event.category is None:
        return None
    val = event.category.value if hasattr(event.category, "value") else str(event.category)
    return val.replace("_", " ").title()


def payment_reference_lines(payment: Optional[Payment]) -> list[tuple[str, str]]:
    if not payment:
        return []
    rows: list[tuple[str, str]] = []
    status = payment.status.value if hasattr(payment.status, "value") else str(payment.status)
    rows.append(("Payment status", status))
    currency = payment.currency or "INR"
    amount = f"₹{payment.amount_paise / 100:.2f}" if currency == "INR" else f"{payment.amount_paise / 100:.2f} {currency}"
    rows.append(("Amount", amount))
    if payment.razorpay_order_id.startswith("admin_offline_"):
        rows.append(("Payment method", "Offline / manual (admin recorded)"))
        rows.append(("Reference", payment.razorpay_order_id))
    elif payment.status == PaymentStatus.PAID and payment.razorpay_payment_id:
        rows.append(("Payment method", "Razorpay"))
        rows.append(("Razorpay payment ID", payment.razorpay_payment_id))
    else:
        rows.append(("Order reference", payment.razorpay_order_id))
    return rows


def coordinator_contact_line(event: Event) -> Optional[str]:
    parts = []
    if event.coordinator:
        parts.append(event.coordinator.strip())
    if event.coord_contact:
        parts.append(event.coord_contact.strip())
    if not parts:
        return None
    return " · ".join(parts)
