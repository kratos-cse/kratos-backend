"""Capacity counts only CONFIRMED registrations; pending does not block or consume slots."""
import uuid

import pytest
from fastapi import HTTPException
from app.core.errors import CAPACITY_FULL, AppError
from app.models.enums import (
    EventRegistrationStatus,
    EventVisibility,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
)
from app.models.event import Event, EventRegistrationRule
from app.models.registration import Registration
from app.payments.apply import apply_payment_success, confirm_solo_registration
from app.schemas.registration import RegistrationCreateRequest, RegistrationType
from app.services.admin_ops_service import event_operations_metrics
from app.services.event_service import batch_spots_remaining, invalidate_spots_cache, spots_remaining
from app.services.registration_service import create_registration

from .conftest import (
    _make_event,
    _make_payment,
    _make_solo_registration,
    _make_user_profile,
    refresh_registration,
    requires_db,
)


@requires_db
@pytest.mark.asyncio
async def test_pending_does_not_consume_capacity(db):
    event = Event(
        id=uuid.uuid4(),
        name="Pending cap",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=1,
        fee=0,
    )
    rules = EventRegistrationRule(
        event_id=event.id,
        registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
        allow_individual=True,
        team_min_size=1,
        team_max_size=1,
    )
    db.add(event)
    db.add(rules)
    await db.flush()

    p1 = await _make_user_profile(db, email=f"p1-{uuid.uuid4().hex}@t.local")
    await _make_solo_registration(db, event=event, profile=p1)
    invalidate_spots_cache()

    assert await spots_remaining(db, event, rules) == 1

    p2 = await _make_user_profile(db, email=f"p2-{uuid.uuid4().hex}@t.local")
    prof = await db.get(type(p2), p2.id)
    await create_registration(
        db,
        event.id,
        prof,
        RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
    )


@requires_db
@pytest.mark.asyncio
async def test_confirmed_registration_makes_event_full(db):
    event = Event(
        id=uuid.uuid4(),
        name="Full cap",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=1,
        fee=0,
    )
    rules = EventRegistrationRule(
        event_id=event.id,
        registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
        allow_individual=True,
        team_min_size=1,
        team_max_size=1,
    )
    db.add(event)
    db.add(rules)
    await db.flush()

    p1 = await _make_user_profile(db, email=f"c1-{uuid.uuid4().hex}@t.local")
    db.add(
        Registration(
            event_id=event.id,
            profile_id=p1.id,
            status=RegistrationStatus.CONFIRMED,
        )
    )
    await db.flush()
    invalidate_spots_cache()

    assert await spots_remaining(db, event, rules) == 0

    p2 = await _make_user_profile(db, email=f"c2-{uuid.uuid4().hex}@t.local")
    prof = await db.get(type(p2), p2.id)
    with pytest.raises(HTTPException) as exc:
        await create_registration(
            db,
            event.id,
            prof,
            RegistrationCreateRequest(registration_type=RegistrationType.SOLO),
        )
    assert "capacity" in str(exc.value.detail).lower()


@requires_db
@pytest.mark.asyncio
async def test_confirm_succeeds_when_capacity_available(db, solo_payment_setup):
    data = solo_payment_setup
    event = data["event"]
    event.capacity = 1
    await db.flush()
    invalidate_spots_cache()

    await apply_payment_success(db, data["payment"].id, f"rzp_{uuid.uuid4().hex}")
    reg = await refresh_registration(db, data["registration"].id)
    assert reg.status == RegistrationStatus.CONFIRMED


@requires_db
@pytest.mark.asyncio
async def test_confirm_fails_when_capacity_full(db):
    event = await _make_event(db, name=f"Cap confirm {uuid.uuid4().hex[:6]}")
    event.capacity = 1
    await db.flush()

    holder = await _make_user_profile(db, email=f"hold-{uuid.uuid4().hex}@t.local")
    db.add(
        Registration(
            event_id=event.id,
            profile_id=holder.id,
            status=RegistrationStatus.CONFIRMED,
        )
    )
    await db.flush()

    payer = await _make_user_profile(db, email=f"pay-{uuid.uuid4().hex}@t.local")
    registration = await _make_solo_registration(db, event=event, profile=payer)
    payment = await _make_payment(
        db,
        payer=payer,
        payment_type=PaymentType.SOLO_REGISTRATION,
        registration=registration,
    )
    await db.flush()

    with pytest.raises(AppError) as exc:
        await confirm_solo_registration(db, payment.id)
    assert exc.value.code == CAPACITY_FULL

    reg = await refresh_registration(db, registration.id)
    assert reg.status == RegistrationStatus.PENDING


@requires_db
@pytest.mark.asyncio
async def test_cancelled_registration_does_not_consume_capacity(db):
    event = Event(
        id=uuid.uuid4(),
        name="Cancelled cap",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=1,
        fee=0,
    )
    rules = EventRegistrationRule(
        event_id=event.id,
        registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
        allow_individual=True,
        team_min_size=1,
        team_max_size=1,
    )
    db.add(event)
    db.add(rules)
    await db.flush()

    p1 = await _make_user_profile(db, email=f"x-{uuid.uuid4().hex}@t.local")
    db.add(
        Registration(
            event_id=event.id,
            profile_id=p1.id,
            status=RegistrationStatus.CANCELLED,
        )
    )
    await db.flush()
    invalidate_spots_cache()

    assert await spots_remaining(db, event, rules) == 1


@requires_db
@pytest.mark.asyncio
async def test_admin_metrics_capacity_used_counts_confirmed_only(db):
    event = Event(
        id=uuid.uuid4(),
        name="Admin metrics",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
        capacity=5,
        fee=0,
    )
    rules = EventRegistrationRule(
        event_id=event.id,
        registration_mode=RegistrationMode.INDIVIDUAL_ONLY,
        allow_individual=True,
        team_min_size=1,
        team_max_size=1,
    )
    db.add(event)
    db.add(rules)
    await db.flush()

    for i in range(2):
        p = await _make_user_profile(db, email=f"m-{i}-{uuid.uuid4().hex}@t.local")
        db.add(
            Registration(
                event_id=event.id,
                profile_id=p.id,
                status=RegistrationStatus.PENDING,
            )
        )
    confirmed = await _make_user_profile(db, email=f"m-c-{uuid.uuid4().hex}@t.local")
    db.add(
        Registration(
            event_id=event.id,
            profile_id=confirmed.id,
            status=RegistrationStatus.CONFIRMED,
        )
    )
    await db.flush()
    invalidate_spots_cache()

    metrics = await event_operations_metrics(db, event.id)
    assert metrics["capacity"] == 5
    assert metrics["capacity_used"] == 1
    assert metrics["spots_remaining"] == 4

    remaining = await batch_spots_remaining(db, [(event, rules)])
    assert remaining[event.id] == 4
