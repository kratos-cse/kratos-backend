import asyncio
import os
from urllib.parse import urlsplit
from uuid import uuid4
import pytest

from app.core.config import settings
from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus
from app.models.payment import Payment
from app.payments.apply import apply_payment_success
from app.services import email_service, notification_service

from tests.conftest import _make_event, _make_solo_registration, _make_user_profile, requires_db

pytestmark = [
    requires_db,
    pytest.mark.skipif(
        os.getenv("RUN_SMTP_INTEGRATION") != "1",
        reason="Set RUN_SMTP_INTEGRATION=1 to run real SMTP integration tests",
    ),
]


@pytest.mark.asyncio
async def test_real_smtp_after_payment(db, monkeypatch):
    recipient = os.getenv("SMTP_TEST_EMAIL", "").strip()
    missing = [
        name
        for name, value in (
            ("SMTP_TEST_EMAIL", recipient),
            ("SMTP_HOST", settings.SMTP_HOST),
            ("SMTP_FROM", settings.SMTP_FROM),
        )
        if not value
    ]
    if missing:
        pytest.skip("Missing required SMTP integration variables: " + ", ".join(missing))

    database_name = urlsplit(settings.async_database_url).path.lstrip("/")
    if database_name != "kratos_test":
        pytest.skip(
            f"Refusing to run against database {database_name!r}; expected dedicated database 'kratos_test'"
        )

    delivery_tasks: list[asyncio.Task] = []

    def capture_task(coro):
        task = asyncio.create_task(coro)
        delivery_tasks.append(task)
        return task

    monkeypatch.setattr(notification_service.asyncio, "create_task", capture_task)

    smtp_result = {}
    real_send_email = email_service.send_email

    async def capture_smtp_result(**kwargs):
        result = await real_send_email(**kwargs)
        smtp_result["result"] = result
        return result

    monkeypatch.setattr(email_service, "send_email", capture_smtp_result)

    profile = await _make_user_profile(db, email=recipient)
    event = await _make_event(db, name=f"SMTP Post-Payment Test {uuid4().hex[:8]}")
    registration = await _make_solo_registration(db, event=event, profile=profile)
    payment = Payment(
        payer_profile_id=profile.id,
        payment_type=PaymentType.SOLO_REGISTRATION,
        razorpay_order_id=f"local-smtp-order-{uuid4().hex}",
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
        f"local-smtp-payment-{uuid4().hex}",
    )

    if delivery_tasks:
        await asyncio.gather(*delivery_tasks)

    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED
    assert smtp_result.get("result") == (True, None)
