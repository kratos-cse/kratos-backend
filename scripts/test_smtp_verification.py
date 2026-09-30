import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch
from app.services.email_service import send_email
from app.services.email_triggers import trigger_email
from app.services.notification_service import _send_email, _send_smtp
from app.core.config import settings

async def test_full_smtp_pipeline():
    settings.SMTP_HOST = "smtp.testserver.com"
    settings.SMTP_PORT = 587
    settings.SMTP_USER = "testuser"
    settings.SMTP_PASSWORD = "testpassword"
    settings.SMTP_FROM = "noreply@kratos.com"
    settings.SMTP_FROM_NAME = "KRATOS'26"
    settings.SMTP_TLS = False

    captured_messages = []

    async def fake_smtp_send(message, **kwargs):
        captured_messages.append({"message": message, "kwargs": kwargs})
        return ({}, "250 OK")

    with patch("aiosmtplib.send", side_effect=fake_smtp_send):
        # 1. Test Plain text email
        ok, err = await send_email(
            to_email="student@example.com",
            subject="Welcome to KRATOS",
            text_body="Your registration is confirmed.",
        )
        assert ok is True, f"Expected True, got {ok}, {err}"
        assert len(captured_messages) == 1
        msg1 = captured_messages[-1]["message"]
        assert msg1["To"] == "student@example.com"
        assert msg1["From"] == "KRATOS'26 <noreply@kratos.com>"
        assert msg1["Subject"] == "Welcome to KRATOS"
        print("[PASS] Plain text email sent successfully with correct From header:", msg1["From"])

        # 2. Test Multipart HTML + Inline Image (QR code)
        fake_qr_png = b"\x89PNG\r\n\x1a\nfakeqrbytes"
        ok, err = await send_email(
            to_email="student@example.com",
            subject="Your QR Pass",
            text_body="Your QR is attached.",
            html_body="<h2>QR Pass</h2><img src=\"cid:participant-qr\">",
            inline_images=[("participant-qr", "image/png", fake_qr_png)],
        )
        assert ok is True
        assert len(captured_messages) == 2
        msg2 = captured_messages[-1]["message"]
        assert msg2["To"] == "student@example.com"
        assert msg2.is_multipart()
        print("[PASS] Multipart HTML + Inline QR Image email sent successfully!")

        # 3. Test notification_service._send_email wrapper
        ok, err = await _send_email(
            "leader@example.com",
            "Team Registration Confirmed",
            "Team Alpha is confirmed.",
            html_body="<p>Team Alpha is confirmed.</p>",
        )
        assert ok is True
        assert len(captured_messages) == 3
        print("[PASS] notification_service._send_email works seamlessly via SMTP!")

        # 4. Test trigger_email
        ok, err = await trigger_email(
            to_email="faculty@example.com",
            subject="Event Announcement",
            text_body="Event starts tomorrow.",
            html_body="<b>Event starts tomorrow.</b>",
        )
        assert ok is True
        assert len(captured_messages) == 4
        print("[PASS] email_triggers.trigger_email works seamlessly via SMTP!")

    print("\nAll SMTP email triggering mechanisms verified and working properly!")

if __name__ == "__main__":
    asyncio.run(test_full_smtp_pipeline())
