import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.htf.errors import DECISION_LOCKED, SCREENING_NOT_ALLOWED
from app.htf.models.application import HTFApplication, HTFEventConfig
from app.htf.models.payment import HTFPayment
from app.htf.models.screening import HTFScreeningResult
from app.htf.services import screening_service
from app.models.admin import AdminUser, Role
from app.models.audit_log import AuditLog
from app.models.enums import EventCategory, EventRegistrationStatus, EventVisibility, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.event import Event
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.models.user import User
from tests.conftest import requires_db


async def _create_screening_setup(db: AsyncSession, *, app_status="PPT_SUBMITTED"):
    user = User(google_sub=f"sub-{uuid.uuid4().hex}", email=f"lead-{uuid.uuid4().hex[:6]}@test.local")
    admin_user = User(google_sub=f"sub-admin-{uuid.uuid4().hex}", email=f"admin-{uuid.uuid4().hex[:6]}@test.local")
    db.add_all([user, admin_user])
    await db.flush()

    leader = Profile(
        user_id=user.id,
        full_name="Leader",
        contact_email=user.email,
        college_name="Test College",
    )
    db.add(leader)
    await db.flush()

    role = Role(id=uuid.uuid4(), name="SUPER_ADMIN", description="Super Admin")
    db.add(role)
    await db.flush()

    admin = AdminUser(
        id=uuid.uuid4(),
        user_id=admin_user.id,
        role_id=role.id,
        is_active=True,
    )
    admin.role = role
    db.add(admin)
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
        name="Team Beta",
        leader_profile_id=leader.id,
        status=TeamStatus.FORMING,
    )
    db.add(team)
    await db.flush()

    member = TeamMember(
        team_id=team.id,
        event_id=event.id,
        profile_id=leader.id,
        role=TeamMemberRole.LEADER,
        status=TeamMemberStatus.ACTIVE,
    )
    db.add(member)
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

    return {"leader": leader, "admin": admin, "event": event, "team": team, "app": app, "config": config}


@requires_db
@pytest.mark.asyncio
async def test_shortlist_application(db: AsyncSession):
    data = await _create_screening_setup(db)

    res = await screening_service.record_screening_decision(
        db,
        application_id=data["app"].id,
        result="SHORTLISTED",
        notes="Strong problem statement and architecture",
        admin=data["admin"],
    )

    assert res.result == "SHORTLISTED"
    assert res.notes == "Strong problem statement and architecture"
    assert res.decided_by_admin_user_id == data["admin"].id

    # Application status should be updated
    app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert app.status == "SHORTLISTED"

    # Audit row must be present
    audits = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.resource_id == str(data["app"].id))
            .order_by(AuditLog.created_at.desc())
        )
    ).all()
    actions = [a.action for a in audits]
    assert "HTF_SCREENING_DECIDED" in actions
    assert "HTF_SHORTLISTED" in actions


@requires_db
@pytest.mark.asyncio
async def test_reject_application(db: AsyncSession):
    data = await _create_screening_setup(db)

    res = await screening_service.record_screening_decision(
        db,
        application_id=data["app"].id,
        result="NOT_SHORTLISTED",
        notes="Scope too vague",
        admin=data["admin"],
    )

    assert res.result == "NOT_SHORTLISTED"
    app = await db.scalar(select(HTFApplication).where(HTFApplication.id == data["app"].id))
    assert app.status == "NOT_SHORTLISTED"


@requires_db
@pytest.mark.asyncio
async def test_repeating_same_result_is_idempotent(db: AsyncSession):
    data = await _create_screening_setup(db)

    res1 = await screening_service.record_screening_decision(
        db,
        application_id=data["app"].id,
        result="SHORTLISTED",
        notes="Note 1",
        admin=data["admin"],
    )
    res2 = await screening_service.record_screening_decision(
        db,
        application_id=data["app"].id,
        result="SHORTLISTED",
        notes="Note 1",
        admin=data["admin"],
    )

    assert res1.id == res2.id
    assert res2.result == "SHORTLISTED"


@requires_db
@pytest.mark.asyncio
async def test_refuse_decision_before_ppt_closes(db: AsyncSession):
    data = await _create_screening_setup(db)
    data["config"].ppt_submission_closes_at = datetime.now(timezone.utc) + timedelta(days=2)
    await db.flush()

    with pytest.raises(AppError) as exc:
        await screening_service.record_screening_decision(
            db,
            application_id=data["app"].id,
            result="SHORTLISTED",
            notes=None,
            admin=data["admin"],
        )
    assert exc.value.code == SCREENING_NOT_ALLOWED


@requires_db
@pytest.mark.asyncio
async def test_cannot_modify_decision_after_payment_created(db: AsyncSession):
    data = await _create_screening_setup(db, app_status="SHORTLISTED")

    # Add a CREATED payment row
    payment = HTFPayment(
        id=uuid.uuid4(),
        application_id=data["app"].id,
        payer_profile_id=data["leader"].id,
        razorpay_order_id=f"order_{uuid.uuid4().hex[:12]}",
        amount_paise=50000,
        currency="INR",
        status="CREATED",
    )
    db.add(payment)
    await db.flush()

    # Try to modify decision
    with pytest.raises(AppError) as exc:
        await screening_service.record_screening_decision(
            db,
            application_id=data["app"].id,
            result="NOT_SHORTLISTED",
            notes="Changed mind",
            admin=data["admin"],
        )
    assert exc.value.code == DECISION_LOCKED

    # Repeating the SAME decision (SHORTLISTED) even with payment existing must succeed!
    res_repeat = await screening_service.record_screening_decision(
        db,
        application_id=data["app"].id,
        result="SHORTLISTED",
        notes="Same decision",
        admin=data["admin"],
    )
    assert res_repeat.result == "SHORTLISTED"
