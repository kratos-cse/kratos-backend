import asyncio
import logging
from typing import Optional

from mailjet_rest import Client

from app.core.config import settings

logger = logging.getLogger("email_service")


def get_mailjet_client() -> Client:
    return Client(
        auth=(
            settings.MAILJET_API_KEY,
            settings.MAILJET_SECRET_KEY,
        ),
        version="v3.1",
    )


async def send_email(
    *,
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> tuple[bool, Optional[str]]:

    if not settings.MAILJET_API_KEY:
        return False, "MAILJET_API_KEY is not configured"

    if not settings.MAILJET_SECRET_KEY:
        return False, "MAILJET_SECRET_KEY is not configured"

    if not settings.MAILJET_FROM_EMAIL:
        return False, "MAILJET_FROM_EMAIL is not configured"

    data = {
        "Messages": [
            {
                "From": {
                    "Email": settings.MAILJET_FROM_EMAIL,
                    "Name": settings.MAILJET_FROM_NAME,
                },
                "To": [
                    {
                        "Email": to_email,
                    }
                ],
                "Subject": subject,
                "TextPart": text_body,
                "HTMLPart": html_body or text_body.replace("\n", "<br>"),
            }
        ]
    }

    try:
        client = get_mailjet_client()

        response = await asyncio.to_thread(
            client.send.create,
            data=data,
        )

        if response.status_code >= 400:
            return False, f"Mailjet {response.status_code}: {response.text}"

        return True, None

    except Exception as exc:
        logger.exception(
            "Mailjet email failed to=%s subject=%s",
            to_email,
            subject,
        )

        return False, str(exc).strip() or exc.__class__.__name__