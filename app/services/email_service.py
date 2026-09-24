"""Async Mailjet sender for the separate transactional email system."""

import asyncio
import html
import logging
from typing import Optional

from mailjet_rest import Client

from app.core.config import settings

logger = logging.getLogger("email_service")


def _mailjet_client() -> Client:
    return Client(
        auth=(settings.MAILJET_API_KEY, settings.MAILJET_SECRET_KEY),
        version="v3.1",
    )


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> tuple[bool, str | None]:
    """Send one email through Mailjet without blocking the event loop."""
    required_settings = (
        ("MAILJET_API_KEY", settings.MAILJET_API_KEY),
        ("MAILJET_SECRET_KEY", settings.MAILJET_SECRET_KEY),
        ("MAILJET_FROM_EMAIL", settings.MAILJET_FROM_EMAIL),
    )
    for name, value in required_settings:
        if not value:
            return False, f"{name} is not configured"

    payload = {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_FROM_EMAIL,
                    "Name": settings.MAILJET_FROM_NAME,
                },
                "To": [{"Email": to_email}],
                "Subject": subject,
                "TextPart": text_body,
                "HTMLPart": html_body or html.escape(text_body).replace("\n", "<br>"),
            }
        ]
    }

    try:
        response = await asyncio.to_thread(_mailjet_client().send.create, data=payload)
        if response.status_code >= 400:
            return False, f"Mailjet {response.status_code}: {response.text}"
        return True, None
    except Exception as exc:
        logger.exception("Mailjet email failed to=%s subject=%s", to_email, subject)
        return False, str(exc).strip() or exc.__class__.__name__
