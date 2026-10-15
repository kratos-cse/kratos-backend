import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError
from app.htf.errors import (
    NOT_SHORTLISTED,
    NOT_TEAM_LEADER,
    PAYMENT_DEADLINE_PASSED,
    PAYMENT_NOT_ELIGIBLE,
)
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.payment import HTFPayment
from app.htf.services import payment_service
from app.models.audit_log import AuditLog
from app.models.enums import EventCategory, EventRegistrationStatus, EventVisibility, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.event import Event
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.models.user import User
from tests.conftest import requires_db


def _sign_checkout(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _sign_webhook(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeRazorpayOrder:
    def __init__(self):
        self.created_orders = []
        self.orders_dict = {}

    def create(self, payload: dict) -> dict:
        order_id = f"order_{uuid.uuid4().hex[:12]}"
        order_data = {"id": order_id, **payload}
        self.created_orders.append(order_data)
        self.orders_dict[order_id] = order_data
        return order_data

    def payments(self, order_id: str) -> dict:
        return {
            "items": [
                {
                    "id": f"pay_{uuid.uuid4().hex[:12]}",
                    "order_id": order_id,
                    "status": "captured",
                    "amount": 50000,
                }
            ]
        }


class FakeRazorpayClient:
    def __init__(self):
        self.order = FakeRazorpayOrder()


async def _create_payment_setup(db: AsyncSession, *, app_status="SHORTLISTED"):
    user = User(google_sub=f"sub-{uuid.uuid4().hex}", email=f"lead-{uuid.uuid4().hex[:6]}@test.local")
    other_user = User(google_sub=f"sub-mem-{uuid.uuid4().hex}", email=f"mem-{uuid.uuid4().hex[:6]}@test.local")
    db.add_all([user, other_user])
    await db.flush()

    leader = Profile(
        user_id=user.id,
        full_name="Leader User",
        contact_email=user.email,
        college_name="Test College",
    )
    member_profile = Profile(
        user_id=other_user.id,
        full_name="Member User",
        contact_email=other_user.email,
        college_name="Test College",
    )
    db.add_all([leader, member_profile])
    await db.flush()

    event = Event(
        name=f"HTF 2026 {uuid.uuid4().hex[:6]}",
        short_desc="Hackathon",
        category=EventCategory.TECHNICAL,
        fee=0,
        venue="Campus",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    db.add(event)
    await db.flush()

    team = Team(
        event_id=event.id,
        name="Team Gamma",
        leader_profile_id=leader.id,
        status=TeamStatus.FORMING,
    )
    db.add(team)
    await db.flush()

    db.add_all([
        TeamMember(
            team_id=team.id,
            event_id=event.id,
            profile_id=leader.id,
            role=TeamMemberRole.LEADER,
            status=TeamMemberStatus.ACTIVE,
        ),
        TeamMember(
            team_id=team.id,
            event_id=event.id,
            profile_id=member_profile.id,
            role=TeamMemberRole.MEMBER,
            status=TeamMemberStatus.ACTIVE,
        ),
    ])
    await db.flush()

    app = HTFApplication(
        id=uuid.uuid4(),
        event_id=event.id,
        team_id=team.id,
        status=app_status,
        created_by_profile_id=leader.id,
    )
    db.add(app)
    await db.flush()

    config = HTFEventConfig(
        id=uuid.uuid4(),
        event_id=event.id,
        payment_amount_paise=50000,
        currency="INR",
        max_team_size=4,
    )
    db.add(config)
    await db.flush()

    return {
        "leader": leader,
        "member": member_profile,
        "event": event,
        "team": team,
        "app": app,
        "config": config,
    }


@requires_db
@pytest.mark.asyncio
async def test_non_shortlisted_cannot_create_order(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="SUBMITTED")

    with pytest.raises(AppError) as exc:
        await payment_service.create_payment_order(
            db,
            application_id=data["app"].id,
            profile=data["leader"],
        )
    assert exc.value.code == PAYMENT_NOT_ELIGIBLE


@requires_db
@pytest.mark.asyncio
async def test_rejected_cannot_create_order(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="NOT_SHORTLISTED")

    with pytest.raises(AppError) as exc:
        await payment_service.create_payment_order(
            db,
            application_id=data["app"].id,
            profile=data["leader"],
        )
    assert exc.value.code == NOT_SHORTLISTED


@requires_db
@pytest.mark.asyncio
async def test_non_leader_cannot_create_order(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")

    with pytest.raises(AppError) as exc:
        await payment_service.create_payment_order(
            db,
            application_id=data["app"].id,
            profile=data["member"],
        )
    assert exc.value.code == NOT_TEAM_LEADER


@requires_db
@pytest.mark.asyncio
async def test_payment_deadline_passed_rejected(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    data["config"].payment_closes_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db.flush()

    with pytest.raises(AppError) as exc:
        await payment_service.create_payment_order(
            db,
            application_id=data["app"].id,
            profile=data["leader"],
        )
    assert exc.value.code == PAYMENT_DEADLINE_PASSED


@requires_db
@pytest.mark.asyncio
async def test_create_order_and_idempotency(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    fake_rzp = FakeRazorpayClient()
    monkeypatch.setattr(payment_service, "get_razorpay", lambda: fake_rzp)

    # First call creates order
    res1 = await payment_service.create_payment_order(
        db,
        application_id=data["app"].id,
        profile=data["leader"],
    )
    assert res1.amountPaise == 50000
    assert res1.currency == "INR"
    assert res1.razorpayOrderId.startswith("order_")

    # Second call returns existing order without creating a new one
    res2 = await payment_service.create_payment_order(
        db,
        application_id=data["app"].id,
        profile=data["leader"],
    )
    assert res1.paymentId == res2.paymentId
    assert res1.razorpayOrderId == res2.razorpayOrderId
    assert len(fake_rzp.order.created_orders) == 1


@requires_db
@pytest.mark.asyncio
async def test_payment_verify_success_and_idempotent(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    test_secret = "test_rzp_key_secret_12345"
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", test_secret)

    order_id = f"order_{uuid.uuid4().hex[:12]}"
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    signature = _sign_checkout(order_id, payment_id, test_secret)

    # Pre-seed CREATED payment row
    pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=order_id,
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(pay)
    await db.flush()

    # 1. Verify payment
    res = await payment_service.verify_payment(
        db,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
        profile=data["leader"],
    )
    assert res["status"] == "success"

    # Status must be updated
    updated_pay = await db.scalar(select(HTFPayment).where(HTFPayment.id == pay.id))
    assert updated_pay.status == "PAID"
    assert updated_pay.razorpay_payment_id == payment_id

    updated_app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert updated_app.status == "CONFIRMED"

    # 2. Repeating verify is safe/idempotent
    res_repeat = await payment_service.verify_payment(
        db,
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=signature,
        profile=data["leader"],
    )
    assert res_repeat["status"] == "success"


@requires_db
@pytest.mark.asyncio
async def test_payment_verify_bad_signature(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    test_secret = "test_rzp_key_secret_12345"
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", test_secret)

    order_id = f"order_{uuid.uuid4().hex[:12]}"
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"
    bad_signature = "bad_hex_signature"

    pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=order_id,
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(pay)
    await db.flush()

    with pytest.raises(AppError) as exc:
        await payment_service.verify_payment(
            db,
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            razorpay_signature=bad_signature,
            profile=data["leader"],
        )
    assert exc.value.code == "INVALID_PAYMENT_SIGNATURE"


@requires_db
@pytest.mark.asyncio
async def test_webhook_captured_confirms_payment(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    webhook_secret = "test_htf_webhook_secret_999"
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_RAZORPAY_WEBHOOK_SECRET", webhook_secret)

    order_id = f"order_{uuid.uuid4().hex[:12]}"
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=order_id,
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(pay)
    await db.flush()

    payload_dict = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 50000,
                }
            }
        },
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = _sign_webhook(raw_body, webhook_secret)

    # Process webhook
    res = await payment_service.handle_webhook(db, raw_body=raw_body, signature=sig)
    assert res["status"] == "ok"

    updated_pay = await db.scalar(select(HTFPayment).where(HTFPayment.id == pay.id))
    assert updated_pay.status == "PAID"
    assert updated_pay.razorpay_payment_id == payment_id

    updated_app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert updated_app.status == "CONFIRMED"

    # Duplicate webhook is idempotent
    res_dup = await payment_service.handle_webhook(db, raw_body=raw_body, signature=sig)
    assert res_dup["status"] == "ok"


@requires_db
@pytest.mark.asyncio
async def test_webhook_ignores_unknown_order(db: AsyncSession, monkeypatch):
    webhook_secret = "test_htf_webhook_secret_999"
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_RAZORPAY_WEBHOOK_SECRET", webhook_secret)

    payload_dict = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_kratos_regular_123",
                    "order_id": "order_kratos_regular_123",
                    "amount": 25000,
                }
            }
        },
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = _sign_webhook(raw_body, webhook_secret)

    res = await payment_service.handle_webhook(db, raw_body=raw_body, signature=sig)
    assert res["status"] == "ok"
    assert res.get("reason") == "unknown order"


@requires_db
@pytest.mark.asyncio
async def test_webhook_amount_mismatch_rejected(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    webhook_secret = "test_htf_webhook_secret_999"
    from app.htf.settings import htf_settings
    monkeypatch.setattr(htf_settings, "HTF_RAZORPAY_WEBHOOK_SECRET", webhook_secret)

    order_id = f"order_{uuid.uuid4().hex[:12]}"
    pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=order_id,
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(pay)
    await db.flush()

    payload_dict = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_123",
                    "order_id": order_id,
                    "amount": 10000,  # mismatch!
                }
            }
        },
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")
    sig = _sign_webhook(raw_body, webhook_secret)

    with pytest.raises(AppError) as exc:
        await payment_service.handle_webhook(db, raw_body=raw_body, signature=sig)
    assert exc.value.code == "AMOUNT_MISMATCH"


@requires_db
@pytest.mark.asyncio
async def test_captured_payment_when_not_shortlisted_orphans_without_confirming(db: AsyncSession):
    # Application is NOT_SHORTLISTED (e.g. revoked after order created)
    data = await _create_payment_setup(db, app_status="NOT_SHORTLISTED")

    order_id = f"order_{uuid.uuid4().hex[:12]}"
    payment_id = f"pay_{uuid.uuid4().hex[:12]}"

    pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=order_id,
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(pay)
    await db.flush()

    # Confirm payment
    outcome = await payment_service.confirm_htf_payment(
        db,
        htf_payment_id=pay.id,
        razorpay_payment_id=payment_id,
    )
    assert outcome == payment_service.ORPHANED

    # Payment is marked PAID
    updated_pay = await db.scalar(select(HTFPayment).where(HTFPayment.id == pay.id))
    assert updated_pay.status == "PAID"

    # Application is NOT confirmed, remains NOT_SHORTLISTED!
    updated_app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert updated_app.status == "NOT_SHORTLISTED"

    # Audit log records HTF_PAYMENT_ORPHANED
    audits = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.resource_id == str(pay.id))
            .order_by(AuditLog.created_at.desc())
        )
    ).all()
    actions = [a.action for a in audits]
    assert "HTF_PAYMENT_ORPHANED" in actions


@requires_db
@pytest.mark.asyncio
async def test_confirmed_application_raises_already_completed(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="CONFIRMED")

    with pytest.raises(AppError) as exc:
        await payment_service.create_payment_order(
            db,
            application_id=data["app"].id,
            profile=data["leader"],
        )
    assert exc.value.code == "HTF_PAYMENT_ALREADY_COMPLETED"


@requires_db
@pytest.mark.asyncio
async def test_sync_payment_with_multiple_past_orders(db: AsyncSession, monkeypatch):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")
    fake_rzp = FakeRazorpayClient()
    monkeypatch.setattr(payment_service, "get_razorpay", lambda: fake_rzp)

    # Pre-seed an older FAILED order
    old_pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=f"order_failed_{uuid.uuid4().hex[:8]}",
        amount_paise=50000,
        currency="INR",
        status="FAILED",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    # And a newer CREATED order
    new_pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=f"order_active_{uuid.uuid4().hex[:8]}",
        amount_paise=50000,
        currency="INR",
        status="CREATED",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add_all([old_pay, new_pay])
    await db.flush()

    # Calling sync_payment should select the newest active order and not throw MultipleResultsFound
    res = await payment_service.sync_payment(
        db,
        application_id=data["app"].id,
        profile=data["leader"],
    )
    assert res["status"] in {"synced", "confirmed"}


@requires_db
@pytest.mark.asyncio
async def test_late_capture_on_failed_order_with_live_order_exists(db: AsyncSession):
    data = await _create_payment_setup(db, app_status="SHORTLISTED")

    # Older FAILED order
    failed_pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=f"order_failed_{uuid.uuid4().hex[:8]}",
        amount_paise=50000,
        currency="INR",
        status="FAILED",
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    # Newer CREATED order
    live_pay = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=f"order_live_{uuid.uuid4().hex[:8]}",
        amount_paise=50000,
        currency="INR",
        status="CREATED",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add_all([failed_pay, live_pay])
    await db.flush()

    # Late capture on the FAILED order
    outcome = await payment_service.confirm_htf_payment(
        db,
        htf_payment_id=failed_pay.id,
        razorpay_payment_id=f"pay_{uuid.uuid4().hex[:8]}",
    )
    # The newer CREATED order is settled to FAILED and failed_pay is promoted to PAID
    # without violating uq_htf_payments_live
    assert outcome == payment_service.CONFIRMED

    updated_failed = await db.scalar(select(HTFPayment).where(HTFPayment.id == failed_pay.id))
    assert updated_failed.status == "PAID"

    updated_live = await db.scalar(select(HTFPayment).where(HTFPayment.id == live_pay.id))
    assert updated_live.status == "FAILED"
