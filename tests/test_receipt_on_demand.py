"""Receipts are generated on demand — no HTML/PDF files on disk."""
import uuid

from app.services.receipt_service import generate_qr_svg, render_html_receipt, render_pdf_receipt


def test_render_pdf_returns_bytes_not_file():
    ctx = {
        "receipt_number": "KR-TEST-0001",
        "payment_id": str(uuid.uuid4()),
        "issued_at_formatted": "01 Jan 2026, 12:00 PM UTC",
        "event_name": "Test Event",
        "payer_name": "Tester",
        "amount_inr": "250.00",
        "currency": "INR",
        "team_name": None,
        "qr_verify_url": "https://example.com/checkin?token=abc",
    }
    pdf = render_pdf_receipt(ctx)
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"


def test_html_escapes_malicious_event_name():
    ctx = {
        "receipt_number": "KR-XSS-TEST",
        "payment_id": str(uuid.uuid4()),
        "razorpay_order_id": "order_x",
        "razorpay_payment_id": "pay_x",
        "payment_type": "SOLO_REGISTRATION",
        "amount_inr": "100.00",
        "currency": "INR",
        "status": "PAID",
        "issued_at_formatted": "01 Jan 2026",
        "payer_name": "<script>alert(1)</script>",
        "payer_email": "a@b.com",
        "payer_phone": "999",
        "payer_college": "College",
        "event_name": "<img onerror=alert(1) src=x>",
        "event_category": "Tech",
        "event_venue": "Hall",
        "event_slot": "Morning",
        "whatsapp_link": None,
        "team_name": None,
        "team_members": [],
        "qr_token": "tok",
        "qr_svg": generate_qr_svg("https://example.com/t"),
        "qr_verify_url": "https://example.com/t",
    }
    html_out = render_html_receipt(ctx)
    assert "<script>" not in html_out
    assert "<img onerror" not in html_out
    assert "&lt;script&gt;" in html_out


def test_receipt_module_has_no_disk_path_helpers():
    from app.services import receipt_service

    assert not hasattr(receipt_service, "receipt_pdf_path")
    assert not hasattr(receipt_service, "receipt_html_path")
    assert hasattr(receipt_service, "render_pdf_receipt")
