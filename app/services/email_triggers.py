"""Business-event entry points for Mailjet emails outside notification_service."""

from app.services.email_service import send_email


async def trigger_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> tuple[bool, str | None]:
    """Dispatch a business-triggered email through the Mailjet service."""
    return await send_email(
        to_email=to_email,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
    )
