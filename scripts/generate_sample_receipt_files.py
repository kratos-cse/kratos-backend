"""Generate standalone sample receipt HTML and PDF files for visual testing."""

import os
import sys
from datetime import datetime, timezone
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.receipt_service import (
    generate_qr_svg,
    generate_receipt_pdf_bytes,
    render_html_receipt,
)


def main():
    payment_id = uuid.uuid4()
    qr_token = f"KRATOS-PASS-{uuid.uuid4().hex[:12].upper()}"
    receipt_number = f"KR-20261015-{uuid.uuid4().hex[:8].upper()}"

    qr_verify_url = f"http://localhost:8000/checkin?token={qr_token}"
    qr_svg = generate_qr_svg(qr_verify_url)

    ctx = {
        "receipt_number": receipt_number,
        "payment_id": str(payment_id),
        "razorpay_order_id": f"order_{uuid.uuid4().hex[:14]}",
        "razorpay_payment_id": f"pay_{uuid.uuid4().hex[:14]}",
        "payment_type": "SOLO_REGISTRATION",
        "amount_inr": "250.00",
        "currency": "INR",
        "status": "PAID",
        "issued_at_formatted": datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC"),
        "payer_name": "Vijay Kiran",
        "payer_email": "vijaykiran2106@gmail.com",
        "payer_phone": "+91 98765 43210",
        "payer_college": "Easwari Engineering College",
        "event_name": "CODE BLITZ '26 (Flagship Hackathon)",
        "event_category": "TECHNICAL",
        "event_venue": "CSE Central Lab & Seminar Hall",
        "event_slot": "October 15, 2026 09:30 AM",
        "whatsapp_link": "https://chat.whatsapp.com/sample-group-invite",
        "team_name": None,
        "team_members": [],
        "qr_token": qr_token,
        "qr_svg": qr_svg,
        "qr_verify_url": qr_verify_url,
    }

    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_receipts")
    os.makedirs(output_dir, exist_ok=True)

    # 1. Render HTML Receipt
    html_content = render_html_receipt(ctx)
    html_path = os.path.join(output_dir, "receipt_preview.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"[HTML Receipt Generated]: {html_path}")

    # 2. Render PDF Receipt
    pdf_bytes = generate_receipt_pdf_bytes(ctx)
    pdf_path = os.path.join(output_dir, "receipt_preview.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    print(f"[PDF Receipt Generated]: {pdf_path}")

    print("\nSample receipts generated successfully!")


if __name__ == "__main__":
    main()
