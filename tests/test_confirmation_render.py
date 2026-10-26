"""Confirmation email HTML/text rendering (no database)."""
import uuid

from app.models.enums import EventRegistrationStatus, EventVisibility, PaymentStatus, PaymentType
from app.models.event import Event
from app.models.payment import Payment
from app.services.confirmation_email import render_confirmation_email_with_qr


def test_confirmation_includes_whatsapp_and_escapes():
    event = Event(
        id=uuid.uuid4(),
        name="Test Event",
        whatsapp_group_link="https://chat.whatsapp.com/InviteLink",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    payment = Payment(
        id=uuid.uuid4(),
        payer_profile_id=uuid.uuid4(),
        payment_type=PaymentType.SOLO_REGISTRATION,
        razorpay_order_id="order_test",
        razorpay_payment_id="pay_test",
        amount_paise=50000,
        status=PaymentStatus.PAID,
    )
    reg_id = uuid.uuid4()
    content = render_confirmation_email_with_qr(
        qr_token="qr-token-abc",
        participant_name="Alice",
        event=event,
        registration_type="SOLO",
        registration_id=reg_id,
        payment=payment,
        receipt_number=None,
        receipt_html=None,
        receipt_pdf=None,
        registration_page_url=f"https://app.test/registrations/{reg_id}",
    )
    assert "Join the Event WhatsApp Group" in content.html_body
    assert "chat.whatsapp.com/InviteLink" in content.html_body
    assert "Razorpay payment ID" in content.html_body
    assert content.qr_png[:8] == b"\x89PNG\r\n\x1a\n"
