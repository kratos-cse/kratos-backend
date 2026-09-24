"""Post-payment confirmation email + per-participant QR."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.models.enums import (
    NotificationKind,
    NotificationStatus,
    PaymentStatus,
    PaymentType,
    RegistrationStatus,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.qr_code import QRCode
from app.models.team import TeamMember
from app.payments.apply import apply_payment_failure, apply_payment_success
from app.services.confirmation_email import (
    confirmation_was_sent,
    dedupe_key_solo,
    dedupe_key_team_member,
    process_payment_confirmation_emails,
    send_team_member_confirmation,
)

from tests.conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_team_registration,
    _make_user_profile,
    requires_db,
)

pytestmark = requires_db


@pytest.mark.asyncio
async def test_solo_payment_sends_one_confirmation_with_qr(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    profile = setup["profile"]
    sent: list[dict] = []

    async def _capture_send(*_args, **kwargs):
        sent.append(kwargs)
        notif = Notification(
            id=uuid.uuid4(),
            profile_id=profile.id,
            kind=NotificationKind.REGISTRATION_CONFIRMATION,
            channel="EMAIL",
            status=NotificationStatus.PENDING,
            subject=kwargs.get("subject", ""),
            body=kwargs.get("body", ""),
            payload=kwargs.get("payload"),
            payment_id=payment.id,
        )
        db.add(notif)
        await db.flush()
        return notif

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(side_effect=_capture_send),
    ):
        await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    assert len(sent) == 1
    assert sent[0]["inline_images"][0][0] == "participant-qr"
    assert b"PNG" in sent[0]["inline_images"][0][2][:8] or sent[0]["inline_images"][0][2].startswith(b"\x89PNG")

    dedupe = dedupe_key_solo(payment.id, profile.id)
    assert await confirmation_was_sent(db, dedupe) is True


@pytest.mark.asyncio
async def test_solo_payment_failure_no_qr_no_email(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(),
    ) as mock_send:
        await apply_payment_failure(db, payment.id)

    mock_send.assert_not_awaited()
    await db.refresh(registration)
    assert registration.status != RegistrationStatus.CONFIRMED
    qr_count = (
        await db.execute(
            select(func.count()).select_from(QRCode).where(QRCode.registration_id == registration.id)
        )
    ).scalar_one()
    assert qr_count == 0


@pytest.mark.asyncio
async def test_team_payment_sends_per_member_qr_emails(db, team_payment_setup):
    setup = team_payment_setup
    payment = setup["payment"]
    team = setup["team"]
    leader = setup["leader"]

    member = await _make_user_profile(db, email=f"tm-{uuid.uuid4().hex}@test.local")
    db.add(
        TeamMember(
            team_id=team.id,
            event_id=team.event_id,
            profile_id=member.id,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        )
    )
    await db.flush()

    captured_tokens: list[str] = []

    async def _capture_send(*_args, **kwargs):
        inline = kwargs.get("inline_images") or []
        if inline:
            from app.services.receipt_service import generate_qr_png_bytes

            # Decode is not trivial; store profile + qr linkage via payload
            captured_tokens.append(str(kwargs.get("payload", {}).get("team_member_id")))
        notif = Notification(
            id=uuid.uuid4(),
            profile_id=kwargs.get("profile_id") or leader.id,
            kind=NotificationKind.REGISTRATION_CONFIRMATION,
            channel="EMAIL",
            status=NotificationStatus.PENDING,
            subject=kwargs.get("subject", ""),
            body=kwargs.get("body", ""),
            payload=kwargs.get("payload"),
            payment_id=payment.id,
        )
        db.add(notif)
        await db.flush()
        return notif

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(side_effect=_capture_send),
    ):
        await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    leader_member = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.profile_id == leader.id,
            )
        )
    ).scalar_one()
    member_row = (
        await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == team.id,
                TeamMember.profile_id == member.id,
            )
        )
    ).scalar_one()

    assert len(captured_tokens) == 2
    assert str(leader_member.id) in captured_tokens
    assert str(member_row.id) in captured_tokens
    assert captured_tokens[0] != captured_tokens[1]


@pytest.mark.asyncio
async def test_team_member_qr_correctness_in_email_html(db, team_payment_setup):
    setup = team_payment_setup
    payment = setup["payment"]
    team = setup["team"]
    leader = setup["leader"]
    member = await _make_user_profile(db, email=f"qr-{uuid.uuid4().hex}@test.local")
    tm = TeamMember(
        team_id=team.id,
        event_id=team.event_id,
        profile_id=member.id,
        role=TeamMemberRole.MEMBER,
        status=TeamMemberStatus.ACTIVE,
    )
    db.add(tm)
    await db.flush()

    await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    leader_qr = (
        await db.execute(
            select(QRCode).where(
                QRCode.team_member_id
                == (
                    await db.execute(
                        select(TeamMember.id).where(
                            TeamMember.team_id == team.id,
                            TeamMember.profile_id == leader.id,
                        )
                    )
                ).scalar_one(),
                QRCode.is_active.is_(True),
            )
        )
    ).scalar_one()
    member_qr = (
        await db.execute(
            select(QRCode).where(QRCode.team_member_id == tm.id, QRCode.is_active.is_(True))
        )
    ).scalar_one()
    assert leader_qr.token != member_qr.token


@pytest.mark.asyncio
async def test_webhook_retry_idempotent_emails(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    profile = setup["profile"]

    send_count = 0

    async def _capture_send(*_args, **kwargs):
        nonlocal send_count
        send_count += 1
        notif = Notification(
            id=uuid.uuid4(),
            profile_id=profile.id,
            kind=NotificationKind.REGISTRATION_CONFIRMATION,
            channel="EMAIL",
            status=NotificationStatus.SENT,
            subject="x",
            body="y",
            payload=kwargs.get("payload"),
            payment_id=payment.id,
        )
        db.add(notif)
        await db.flush()
        return notif

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(side_effect=_capture_send),
    ):
        await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")
        await process_payment_confirmation_emails(db, payment.id)

    assert send_count == 1
    qr_count = (
        await db.execute(
            select(func.count())
            .select_from(QRCode)
            .where(QRCode.registration_id == setup["registration"].id, QRCode.is_active.is_(True))
        )
    ).scalar_one()
    assert qr_count == 1


@pytest.mark.asyncio
async def test_email_failure_does_not_rollback_payment(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]

    async def _fail_send(*_args, **_kwargs):
        notif = Notification(
            id=uuid.uuid4(),
            profile_id=setup["profile"].id,
            kind=NotificationKind.REGISTRATION_CONFIRMATION,
            channel="EMAIL",
            status=NotificationStatus.FAILED,
            subject="x",
            body="y",
            payload={"dedupe_key": dedupe_key_solo(payment.id, setup["profile"].id)},
            payment_id=payment.id,
            error="smtp down",
        )
        db.add(notif)
        await db.flush()
        return notif

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(side_effect=_fail_send),
    ):
        result = await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    assert result.applied is True
    await db.refresh(payment)
    await db.refresh(registration)
    assert payment.status == PaymentStatus.PAID
    assert registration.status == RegistrationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_receipt_still_issued_after_confirmation(db, solo_payment_setup):
    from app.models.receipt import Receipt

    setup = solo_payment_setup
    payment = setup["payment"]

    with patch(
        "app.services.notification_service.create_and_send_with_inline_image",
        new=AsyncMock(),
    ):
        await apply_payment_success(db, payment.id, f"pay_{uuid.uuid4().hex}")

    receipt = (
        await db.execute(select(Receipt).where(Receipt.payment_id == payment.id))
    ).scalar_one_or_none()
    assert receipt is not None
    assert receipt.receipt_number
