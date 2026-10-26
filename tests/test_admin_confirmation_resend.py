"""Admin confirmation email resend — eligibility and idempotency (mocked SMTP)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import NotificationStatus, PaymentStatus, RegistrationStatus
from app.services import admin_confirmation_resend


@pytest.mark.asyncio
async def test_preview_ineligible_when_not_confirmed():
    from app.models.registration import Registration
    from app.models.event import Event
    from app.models.payment import Payment
    from app.models.enums import EventRegistrationStatus, EventVisibility, PaymentType

    reg_id = uuid.uuid4()
    event = Event(
        id=uuid.uuid4(),
        name="E",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    payment = Payment(
        id=uuid.uuid4(),
        payer_profile_id=uuid.uuid4(),
        payment_type=PaymentType.SOLO_REGISTRATION,
        razorpay_order_id="o1",
        amount_paise=100,
        status=PaymentStatus.PAID,
    )
    reg = Registration(
        id=reg_id,
        event_id=event.id,
        profile_id=uuid.uuid4(),
        payment_id=payment.id,
        status=RegistrationStatus.PENDING,
    )
    reg.event = event
    reg.payment = payment
    reg.team = None

    db = AsyncMock()

    async def fake_load(_db, rid):
        assert rid == reg_id
        return reg

    with patch.object(admin_confirmation_resend, "_load_registration", fake_load):
        preview = await admin_confirmation_resend.preview_resend_confirmation(db, reg_id)

    assert preview.eligible is False
    assert "CONFIRMED" in (preview.reason or "")


@pytest.mark.asyncio
async def test_execute_requires_client_request_id():
    db = AsyncMock()
    with pytest.raises(ValueError, match="client_request_id"):
        await admin_confirmation_resend.execute_resend_confirmation(
            db,
            registration_id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            client_request_id="short",
        )


@pytest.mark.asyncio
async def test_idempotent_replay_detected():
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []

    class FakeDB:
        async def execute(self, _q):
            return empty_result

    db = FakeDB()
    with patch.object(
        admin_confirmation_resend,
        "_admin_resend_request_exists",
        AsyncMock(return_value=True),
    ):
        outcome = await admin_confirmation_resend.execute_resend_confirmation(
            db,
            registration_id=uuid.uuid4(),
            admin_user_id=uuid.uuid4(),
            client_request_id="req-12345678",
        )
    assert outcome.idempotent_replay is True
