"""Payment model: only SOLO_REGISTRATION and TEAM_REGISTRATION are active."""
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.models.enums import PaymentType, TeamMemberStatus


def test_payment_types_have_no_member_topup():
    assert set(PaymentType) == {PaymentType.SOLO_REGISTRATION, PaymentType.TEAM_REGISTRATION}
    assert "TEAM_MEMBER_TOPUP" not in PaymentType.__members__


def test_create_order_body_rejects_topup_literal():
    from app.api.v1.endpoints.payments import CreateOrderBody

    with pytest.raises(ValidationError):
        CreateOrderBody(
            event_id=UUID("00000000-0000-4000-8000-000000000099"),
            payment_type="TEAM_MEMBER_TOPUP",  # type: ignore[arg-type]
        )


def test_leader_awaits_payment_status_exists():
    assert TeamMemberStatus.PENDING_PAYMENT.value == "PENDING_PAYMENT"
    assert TeamMemberStatus.ACTIVE.value == "ACTIVE"
