"""Send a full test event confirmation email with Event details, Payment/Receipt details, and Entry QR Code."""

import asyncio
from datetime import datetime, timezone
import os
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.confirmation_email import (
    QR_CONTENT_ID,
    render_confirmation_email_with_qr,
)
from app.services.email_service import send_email


async def main() -> int:
    recipient = os.getenv("SMTP_TEST_EMAIL", "vijaykiran2106@gmail.com").strip()
    if not recipient:
        print("Recipient email is required.", file=sys.stderr)
        return 1

    # 1. Sample Event
    sample_event = SimpleNamespace(
        name="CODE BLITZ '26 (Flagship Hackathon)",
        starts_at=datetime(2026, 10, 15, 9, 30, tzinfo=timezone.utc),
        venue="CSE Central Lab & Seminar Hall, Easwari Engineering College, Chennai",
    )

    # 2. Sample Registration & Payment
    sample_reg_id = uuid4()
    sample_payment_id = uuid4()
    sample_payment = SimpleNamespace(
        id=sample_payment_id,
        amount_paise=25000,  # Rs. 250.00
    )

    # 3. Sample QR Token & Receipts
    sample_qr_token = f"KRATOS-PASS-{uuid4().hex[:12].upper()}"
    sample_receipt_number = f"REC-2026-{uuid4().hex[:6].upper()}"
    receipt_html = f"http://localhost:8000/api/v1/payments/{sample_payment_id}/receipt/html"
    receipt_pdf = f"http://localhost:8000/api/v1/payments/{sample_payment_id}/receipt/pdf"
    reg_page_url = "http://localhost:3000/dashboard/registrations"

    print(f"Generating branded test confirmation email for: {recipient}")
    print(f"  Event: {sample_event.name}")
    print(f"  Receipt: {sample_receipt_number}")
    print(f"  QR Token: {sample_qr_token}")

    content = render_confirmation_email_with_qr(
        qr_token=sample_qr_token,
        participant_name="Vijay Kiran",
        event=sample_event,
        registration_type="SOLO",
        registration_id=sample_reg_id,
        payment=sample_payment,
        receipt_number=sample_receipt_number,
        receipt_html=receipt_html,
        receipt_pdf=receipt_pdf,
        registration_page_url=reg_page_url,
    )

    print("Sending email with inline QR attachment via SMTP...")
    success, error = await send_email(
        to_email=recipient,
        subject=content.subject,
        text_body=content.text_body,
        html_body=content.html_body,
        inline_images=[(QR_CONTENT_ID, "image/png", content.qr_png)],
    )

    if success:
        print("[SUCCESS] Branded confirmation test email sent successfully!")
        return 0

    print(f"[FAILED] Email sending failed: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
