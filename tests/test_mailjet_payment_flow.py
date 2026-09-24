from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.enums import PaymentStatus, PaymentType
from app.payments import apply as payment_apply
from app.services import email_triggers, notification_service


@pytest.mark.asyncio
async def test_payment_success_triggers_mailjet_after_commit(monkeypatch):
    payment_id = uuid4()
    payer_profile_id = uuid4()
    registration_id = uuid4()
    event_id = uuid4()
    event_name = "KRATOS Test Event"
    recipient = "participant@example.com"
    execution = []
    trigger_kwargs = {}

    payment = SimpleNamespace(
        id=payment_id,
        payment_type=PaymentType.SOLO_REGISTRATION,
        team_member_id=None,
        payer_profile_id=payer_profile_id,
        amount_paise=25000,
    )
    registration = SimpleNamespace(id=registration_id, event_id=event_id)
    event = SimpleNamespace(id=event_id, name=event_name)

    class FakeResult:
        def one_or_none(self):
            return registration, event

    class FakeDatabase:
        async def commit(self):
            execution.append("commit")

        async def execute(self, statement):
            return FakeResult()

    async def fake_trigger_email(**kwargs):
        execution.append("trigger_email")
        trigger_kwargs.update(kwargs)
        return await email_triggers.trigger_email(**kwargs)

    async def fake_send_email(**kwargs):
        execution.append("email_service.send_email")
        return True, None

    monkeypatch.setattr(payment_apply, "transition_payment_status", AsyncMock(return_value=payment))
    monkeypatch.setattr(payment_apply, "confirm_solo_registration", AsyncMock())
    monkeypatch.setattr(payment_apply, "_run_handoffs", AsyncMock())
    monkeypatch.setattr(payment_apply, "trigger_email", fake_trigger_email)
    monkeypatch.setattr(notification_service, "_resolve_email", AsyncMock(return_value=recipient))
    smtp_notification = AsyncMock()
    monkeypatch.setattr(notification_service, "notify_payment_confirmed", smtp_notification)
    monkeypatch.setattr(email_triggers, "send_email", fake_send_email)
    monkeypatch.setattr("app.services.admin_ops_service.invalidate_dashboard_cache", lambda: None)
    monkeypatch.setattr("app.services.event_service.invalidate_spots_cache", lambda: None)

    db = FakeDatabase()
    result = await payment_apply.apply_payment_success(db, payment_id, "razorpay-payment-id")

    assert result.applied is True
    assert result.payment.id == payment_id
    assert execution == ["commit", "trigger_email", "email_service.send_email"]
    notification_service._resolve_email.assert_awaited_once_with(db, payer_profile_id)
    assert trigger_kwargs["to_email"] == recipient
    assert trigger_kwargs["subject"] == f"Payment confirmed - {event_name}"
    assert event_name in trigger_kwargs["text_body"]
    assert str(registration_id) in trigger_kwargs["text_body"]
    assert str(payment_id) in trigger_kwargs["text_body"]
    assert "INR 250.00" in trigger_kwargs["text_body"]
    smtp_notification.assert_awaited_once_with(db, payment_id)
