"""Async Mailjet sender for the separate transactional email system."""

import asyncio
import base64
import html
import logging
from typing import Optional, Sequence

from mailjet_rest import Client

from app.core.config import settings

logger = logging.getLogger("email_service")

InlineImage = tuple[str, str, bytes]  # (content_id, mime_type, data)


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
    inline_images: Optional[Sequence[InlineImage]] = None,
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

    message: dict = {
        "From": {
            "Email": settings.MAILJET_FROM_EMAIL,
            "Name": settings.MAILJET_FROM_NAME,
        },
        "To": [{"Email": to_email}],
        "Subject": subject,
        "TextPart": text_body,
        "HTMLPart": html_body or html.escape(text_body).replace("\n", "<br>"),
    }
    if inline_images:
        message["InlinedAttachments"] = [
            {
                "ContentType": mime,
                "Filename": f"{cid}.png",
                "ContentID": cid,
                "Base64Content": base64.b64encode(data).decode("ascii"),
            }
            for cid, mime, data in inline_images
        ]
    payload = {"Messages": [message]}

    try:
        response = await asyncio.to_thread(_mailjet_client().send.create, data=payload)
        if response.status_code >= 400:
            return False, f"Mailjet {response.status_code}: {response.text}"
        return True, None
    except Exception as exc:
        logger.exception("Mailjet email failed to=%s subject=%s", to_email, subject)
        return False, str(exc).strip() or exc.__class__.__name__
