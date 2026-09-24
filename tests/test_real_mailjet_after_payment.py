import os
from urllib.parse import urlsplit
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_success
from app.services import email_triggers, notification_service

from tests.conftest import _make_event, _make_solo_registration, _make_user_profile, requires_db

pytestmark = requires_db


@pytest.mark.asyncio
async def test_real_mailjet_after_payment(db, monkeypatch):
    recipient = os.getenv("MAILJET_TEST_EMAIL", "").strip()
    missing = [
        name
        for name, value in (
            ("MAILJET_TEST_EMAIL", recipient),
            ("MAILJET_API_KEY", settings.MAILJET_API_KEY),
            ("MAILJET_SECRET_KEY", settings.MAILJET_SECRET_KEY),
            ("MAILJET_FROM_EMAIL", settings.MAILJET_FROM_EMAIL),
        )
        if not value
    ]
    if missing:
        pytest.fail("Missing required Mailjet integration variables: " + ", ".join(missing))

    database_name = urlsplit(settings.async_database_url).path.lstrip("/")
    if database_name != "kratos_test":
        pytest.fail(
            f"Refusing to run against database {database_name!r}; expected dedicated database 'kratos_test'"
        )

    smtp_notification = AsyncMock()
    monkeypatch.setattr(notification_service, "notify_payment_confirmed", smtp_notification)

    mailjet_result = {}
    real_send_email = email_triggers.send_email

    async def capture_mailjet_result(**kwargs):
        result = await real_send_email(**kwargs)
        mailjet_result["result"] = result
        return result

    monkeypatch.setattr(email_triggers, "send_email", capture_mailjet_result)

    profile = await _make_user_profile(db, email=recipient)
    event = await _make_event(db, name=f"Mailjet Post-Payment Test {uuid4().hex[:8]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = Payment(
        payer_profile_id=profile.id,
        payment_type=PaymentType.SOLO_REGISTRATION,
        razorpay_order_id=f"local-mailjet-order-{uuid4().hex}",
        amount_paise=25000,
        currency="INR",
        status=PaymentStatus.CREATED,
    )
    db.add(payment)
    await db.flush()
    registration.payment_id = payment.id
    await db.flush()

    result = await apply_payment_success(
        db,
        payment.id,
        f"local-mailjet-payment-{uuid4().hex}",
    )

    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED
    assert mailjet_result.get("result") == (True, None)
    smtp_notification.assert_awaited_once_with(db, payment.id)
