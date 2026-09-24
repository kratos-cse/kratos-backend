from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.enums import PaymentStatus, PaymentType
from app.payments import apply as payment_apply
from app.services import notification_service


@pytest.mark.asyncio
async def test_payment_success_sends_single_payment_notification(monkeypatch):
    payment_id = uuid4()
    payment = SimpleNamespace(
        id=payment_id,
        payment_type=PaymentType.SOLO_REGISTRATION,
        team_member_id=None,
        payer_profile_id=uuid4(),
        amount_paise=25000,
    )

    class FakeDatabase:
        async def commit(self):
            return None

    notify = AsyncMock()
    monkeypatch.setattr(payment_apply, "transition_payment_status", AsyncMock(return_value=payment))
    monkeypatch.setattr(payment_apply, "confirm_solo_registration", AsyncMock())
    monkeypatch.setattr(payment_apply, "_run_handoffs", AsyncMock())
    monkeypatch.setattr(notification_service, "notify_payment_confirmed", notify)
    monkeypatch.setattr("app.services.admin_ops_service.invalidate_dashboard_cache", lambda: None)
    monkeypatch.setattr("app.services.event_service.invalidate_spots_cache", lambda: None)

    result = await payment_apply.apply_payment_success(FakeDatabase(), payment_id, "razorpay-payment-id")

    assert result.applied is True
    assert result.payment.id == payment_id
    notify.assert_awaited_once()
    assert notify.await_args.args[1] == payment_id


@pytest.mark.asyncio
async def test_deliver_notification_uses_mailjet_when_configured(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "MAILJET_API_KEY", "test-key")
    monkeypatch.setattr(settings, "MAILJET_SECRET_KEY", "test-secret")
    monkeypatch.setattr(settings, "MAILJET_FROM_EMAIL", "noreply@example.com")

    mailjet_send = AsyncMock(return_value=(True, None))
    smtp_send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("app.services.email_service.send_email", mailjet_send)
    monkeypatch.setattr(notification_service, "_send_smtp", smtp_send)

    ok, err = await notification_service._send_email(
        "participant@example.com",
        "Payment confirmed",
        "Thanks for your payment.",
        html_body="<p>Thanks</p>",
    )

    assert ok is True
    assert err is None
    mailjet_send.assert_awaited_once()
    smtp_send.assert_not_awaited()
