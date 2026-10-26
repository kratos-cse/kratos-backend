"""Single SMTP email delivery path for all KRATOS transactional and campaign mail."""

import asyncio
import logging
from email import encoders
from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Sequence

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger("email_service")

InlineImage = tuple[str, str, bytes]  # (content_id, mime_type, data)

SMTP_SEND_TIMEOUT_SEC = 10.0
SMTP_WRAP_TIMEOUT_SEC = 15.0


def smtp_configuration_error() -> Optional[str]:
    """Return a user-facing error when SMTP is not usable, else None."""
    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        return "SMTP not configured (SMTP_HOST / SMTP_FROM missing)"
    if settings.SMTP_SSL and settings.SMTP_TLS:
        return "Invalid SMTP config: enable only one of SMTP_TLS (STARTTLS) or SMTP_SSL (implicit TLS)"
    return None


def _smtp_connect_kwargs() -> dict:
    use_implicit_tls = settings.SMTP_SSL or settings.SMTP_PORT == 465
    if use_implicit_tls and not settings.SMTP_SSL and settings.SMTP_PORT == 465:
        use_implicit_tls = True
    return {
        "hostname": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "username": settings.SMTP_USER or None,
        "password": settings.SMTP_PASSWORD or None,
        "use_tls": use_implicit_tls,
        "start_tls": settings.SMTP_TLS and not use_implicit_tls,
        "timeout": SMTP_SEND_TIMEOUT_SEC,
    }


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    inline_images: Optional[Sequence[InlineImage]] = None,
) -> tuple[bool, str | None]:
    """Send one email via SMTP. Failures return (False, message); never raise to callers."""
    config_err = smtp_configuration_error()
    if config_err:
        return False, config_err

    if inline_images:
        message = MIMEMultipart("related")
        message["From"] = settings.SMTP_FROM
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
        message["From"] = settings.SMTP_FROM
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

    try:
        await asyncio.wait_for(
            aiosmtplib.send(message, **_smtp_connect_kwargs()),
            timeout=SMTP_WRAP_TIMEOUT_SEC,
        )
        return True, None
    except Exception as exc:
        logger.exception("SMTP send failed to=%s subject=%s", to_email, subject)
        err = str(exc).strip() or exc.__class__.__name__
        return False, err
