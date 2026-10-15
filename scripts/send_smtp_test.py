"""Manual SMTP smoke test — not collected by pytest.

Usage:
    set SMTP_TEST_EMAIL=you@example.com
    python scripts/send_smtp_test.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.email_service import send_email


async def main() -> int:
    recipient = os.getenv("SMTP_TEST_EMAIL", "").strip()
    if not recipient:
        print("Set SMTP_TEST_EMAIL before running this script.", file=sys.stderr)
        return 1

    success, error = await send_email(
        to_email=recipient,
        subject="KRATOS SMTP Test",
        text_body=(
            "Hello!\n\n"
            "This is a test email from the KRATOS backend using SMTP.\n\n"
            "KRATOS'26\n"
        ),
        html_body=(
            "<h2>KRATOS SMTP Test</h2>"
            "<p>Hello!</p>"
            "<p>This is a test email from the KRATOS backend using SMTP.</p>"
            "<p><strong>KRATOS'26</strong></p>"
        ),
    )

    if success:
        print("SMTP email sent successfully.")
        return 0

    print("SMTP email failed:", error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
