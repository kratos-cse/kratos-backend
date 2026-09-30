"""Async SMTP sender for transactional email delivery."""

import asyncio
from email import encoders
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
import logging
from typing import Optional, Sequence

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger("email_service")

InlineImage = tuple[str, str, bytes]  # (content_id, mime_type, data)


def _from_address() -> str:
    """Format the sender address with optional display name."""
    from_name = getattr(settings, "SMTP_FROM_NAME", "")
    from_email = settings.SMTP_FROM
    if from_name and from_email:
        return formataddr((from_name, from_email))
    return from_email


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    inline_images: Optional[Sequence[InlineImage]] = None,
) -> tuple[bool, str | None]:
    """Send one email through SMTP without blocking the event loop."""
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return False, "SMTP not configured (SMTP_HOST / SMTP_FROM missing)"

    if inline_images:
        message = MIMEMultipart("related")
        message["From"] = _from_address()
        message["To"] = to_email
        message["Subject"] = subject

        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            alt.attach(MIMEText(html_body, "html", "utf-8"))
        message.attach(alt)

        for cid, mime, data in inline_images:
            subtype = mime.split("/")[-1] if "/" in mime else "png"
            img = MIMEImage(data, _subtype=subtype)
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            encoders.encode_base64(img)
            message.attach(img)
    else:
        message = EmailMessage()
        message["From"] = _from_address()
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

    password = settings.SMTP_PASSWORD.replace(" ", "").strip() if settings.SMTP_PASSWORD else None
    try:
        await asyncio.wait_for(
            aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=settings.SMTP_USER or None,
                password=password,
                start_tls=settings.SMTP_TLS,
                timeout=15.0,
            ),
            timeout=20.0,
        )
        return True, None
    except Exception as exc:
        logger.exception("SMTP email failed to=%s subject=%s", to_email, subject)
        err = str(exc).strip() or exc.__class__.__name__
        return False, err
