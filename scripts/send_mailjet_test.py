"""Manual Mailjet smoke test — not collected by pytest.

Usage:
    set MAILJET_TEST_EMAIL=you@example.com
    python scripts/send_mailjet_test.py
"""
import asyncio
import os
import sys

from app.services.email_service import send_email


async def main() -> int:
    recipient = os.getenv("MAILJET_TEST_EMAIL", "").strip()
    if not recipient:
        print("Set MAILJET_TEST_EMAIL before running this script.", file=sys.stderr)
        return 1

    success, error = await send_email(
        to_email=recipient,
        subject="KRATOS Mailjet Test",
        text_body=(
            "Hello!\n\n"
            "This is a test email from the KRATOS backend using Mailjet.\n\n"
            "KRATOS'26\n"
        ),
        html_body=(
            "<h2>KRATOS Mailjet Test</h2>"
            "<p>Hello!</p>"
            "<p>This is a test email from the KRATOS backend using Mailjet.</p>"
            "<p><strong>KRATOS'26</strong></p>"
        ),
    )

    if success:
        print("Mailjet email sent successfully.")
        return 0

    print("Mailjet email failed:", error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
