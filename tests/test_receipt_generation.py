import uuid
from datetime import datetime, timezone

from app.main import app
from app.models.registration import Registration
from app.services.receipt_service import generate_qr_svg, render_html_receipt


def test_get_current_user_requires_bearer_only():
    import inspect

    from app.core.security import get_current_user

    params = inspect.signature(get_current_user).parameters
    assert "token" not in params


def test_receipt_access_token_routes_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/payments/{payment_id}/receipt/access-token" in paths
    assert "/api/v1/registrations/{registration_id}/receipt/access-token" in paths


def test_registration_model_exposes_event_relationship():
    """Receipt service uses Registration.event — ORM must define it."""
    assert "event" in Registration.__mapper__.relationships


def test_receipt_routes_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/registrations/{registration_id}/receipt" in paths
    assert "/api/v1/registrations/{registration_id}/receipt/html" in paths
    assert "/api/v1/registrations/{registration_id}/receipt/pdf" in paths
    assert "/api/v1/payments/{payment_id}/receipt" in paths
    assert "/api/v1/payments/{payment_id}/receipt/html" in paths
    assert "/api/v1/payments/{payment_id}/receipt/pdf" in paths


def test_email_html_contains_brand():
    from app.services.notification_service import render_email_html

    rendered = render_email_html(
        title="Payment confirmed",
        greeting="You're all set for Code Sprint.",
        paragraphs=["We've received your payment.", "Bring your QR on event day."],
        details=[("Amount paid", "₹250.00"), ("Receipt number", "KR-TEST")],
        cta_url="http://localhost:8000/api/v1/payments/x/receipt/html",
        cta_label="View receipt & pass",
    )
    assert "KRATOS" in rendered
    assert "Payment confirmed" in rendered
    assert "View receipt &amp; pass" in rendered or "View receipt & pass" in rendered
    assert "₹250.00" in rendered


def test_generate_qr_svg():
    svg = generate_qr_svg("https://kratos.cse/checkin?token=test_token_123")
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "<path" in svg


def test_render_html_receipt_solo():
    ctx = {
        "receipt_number": "KR-20260922-A1B2",
        "payment_id": str(uuid.uuid4()),
        "razorpay_order_id": "order_test_999",
        "razorpay_payment_id": "pay_test_888",
        "payment_type": "SOLO_REGISTRATION",
        "amount_inr": "250.00",
        "currency": "INR",
        "status": "PAID",
        "issued_at_formatted": "22 Sep 2026, 07:40 PM UTC",
        "payer_name": "Ada Lovelace",
        "payer_email": "ada@example.com",
        "payer_phone": "+91 9876543210",
        "payer_college": "MIT Campus",
        "event_name": "Algorithmic Code Sprint",
        "event_category": "Technical",
        "event_venue": "CS Lab 3",
        "event_slot": "Day 1, 10:00 AM",
        "whatsapp_link": "https://chat.whatsapp.com/sample",
        "team_name": None,
        "team_members": [],
        "qr_token": "kratos_token_sample_123",
        "qr_svg": generate_qr_svg("https://kratos.cse/checkin?token=kratos_token_sample_123"),
        "qr_verify_url": "https://kratos.cse/checkin?token=kratos_token_sample_123",
    }
    rendered = render_html_receipt(ctx)
    assert "<!DOCTYPE html>" in rendered
    assert "KRATOS" in rendered
    assert "KR-20260922-A1B2" in rendered
    assert "Ada Lovelace" in rendered
    assert "ada@example.com" in rendered
    assert "Algorithmic Code Sprint" in rendered
    assert "₹250.00" in rendered
    assert "pay_test_888" in rendered
    assert "<svg" in rendered
    assert "window.print()" in rendered


def test_render_html_receipt_team():
    ctx = {
        "receipt_number": "KR-20260922-TEAM1",
        "payment_id": str(uuid.uuid4()),
        "razorpay_order_id": "order_team_001",
        "razorpay_payment_id": "pay_team_001",
        "payment_type": "TEAM_REGISTRATION",
        "amount_inr": "1000.00",
        "currency": "INR",
        "status": "PAID",
        "issued_at_formatted": "22 Sep 2026, 07:45 PM UTC",
        "payer_name": "Grace Hopper",
        "payer_email": "grace@example.com",
        "payer_phone": "+91 9123456780",
        "payer_college": "Yale University",
        "event_name": "Hackathon Championship",
        "event_category": "Hackathon",
        "event_venue": "Main Arena",
        "event_slot": "Day 2, 09:00 AM",
        "whatsapp_link": None,
        "team_name": "Bug Hunters",
        "team_members": [
            {"name": "Grace Hopper", "role": "LEADER", "status": "ACTIVE"},
            {"name": "Alan Turing", "role": "MEMBER", "status": "ACTIVE"},
        ],
        "qr_token": "kratos_team_token_999",
        "qr_svg": generate_qr_svg("https://kratos.cse/checkin?token=kratos_team_token_999"),
        "qr_verify_url": "https://kratos.cse/checkin?token=kratos_team_token_999",
    }
    rendered = render_html_receipt(ctx)
    assert "Bug Hunters" in rendered
    assert "Grace Hopper" in rendered
    assert "Alan Turing" in rendered
    assert "LEADER" in rendered
    assert "₹1000.00" in rendered
