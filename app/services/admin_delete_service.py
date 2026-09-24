"""SUPER ADMIN hard-delete — explicit cascade, blocks paid financial records."""
import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.admin import SUPER_ADMIN_ROLE_NAME, AdminUser, Role
from app.models.attendance import AttendanceCheckpoint, AttendanceScan
from app.models.enums import PaymentStatus, RegistrationStatus, TeamMemberStatus
from app.models.event import Event, EventRegistrationRule
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.registration import Registration
from app.models.receipt import Receipt
from app.models.team import Team, TeamInvitation, TeamMember
from app.services import audit_service
from app.api.deps_admin import invalidate_admin_cache
from app.services.event_service import invalidate_events_list_cache, invalidate_spots_cache

_BLOCKED_PAYMENT = frozenset({PaymentStatus.PAID, PaymentStatus.REFUNDED})
_TERMINAL_MEMBER = frozenset({TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED})


def _audit(admin: AdminUser, action: str, resource_type: str, resource_id: uuid.UUID, **details) -> None:
    audit_service.log_activity_bg(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_user_id=admin.user_id,
        actor_role="SUPER_ADMIN",
        details=details,
    )


async def _get_payment(db: AsyncSession, payment_id: uuid.UUID) -> Optional[Payment]:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    return result.scalar_one_or_none()


def _assert_payment_deletable(payment: Optional[Payment]) -> None:
    if payment is None:
        return
    if payment.status in _BLOCKED_PAYMENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Paid or refunded payments block deletion",
        )


async def _purge_qr_ids(db: AsyncSession, qr_ids: list[uuid.UUID]) -> None:
    if not qr_ids:
        return
    await db.execute(delete(AttendanceScan).where(AttendanceScan.qr_id.in_(qr_ids)))
    await db.execute(delete(QRCode).where(QRCode.id.in_(qr_ids)))


async def _collect_qr_ids_for_registration(db: AsyncSession, registration_id: uuid.UUID) -> list[uuid.UUID]:
    result = await db.execute(select(QRCode.id).where(QRCode.registration_id == registration_id))
    return [row[0] for row in result.all()]


async def _collect_qr_ids_for_team(db: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    member_ids = await db.execute(select(TeamMember.id).where(TeamMember.team_id == team_id))
    ids = [row[0] for row in member_ids.all()]
    if not ids:
        return []
    result = await db.execute(select(QRCode.id).where(QRCode.team_member_id.in_(ids)))
    return [row[0] for row in result.all()]


async def _purge_notifications(
    db: AsyncSession,
    *,
    registration_id: Optional[uuid.UUID] = None,
    team_id: Optional[uuid.UUID] = None,
    payment_id: Optional[uuid.UUID] = None,
    profile_id: Optional[uuid.UUID] = None,
) -> None:
    clauses = []
    if registration_id is not None:
        clauses.append(Notification.registration_id == registration_id)
    if team_id is not None:
        clauses.append(Notification.team_id == team_id)
    if payment_id is not None:
        clauses.append(Notification.payment_id == payment_id)
    if profile_id is not None:
        clauses.append(Notification.profile_id == profile_id)
    if not clauses:
        return
    await db.execute(delete(Notification).where(or_(*clauses)))


async def _purge_payment_row(db: AsyncSession, payment_id: uuid.UUID) -> None:
    payment = await _get_payment(db, payment_id)
    if payment is None:
        return
    _assert_payment_deletable(payment)
    await _purge_notifications(db, payment_id=payment_id)
    await db.execute(delete(Receipt).where(Receipt.payment_id == payment_id))
    await db.execute(update(Registration).where(Registration.payment_id == payment_id).values(payment_id=None))
    await db.execute(delete(Payment).where(Payment.id == payment_id))


async def _purge_team_graph(db: AsyncSession, team_id: uuid.UUID, registration: Optional[Registration]) -> None:
    qr_ids = await _collect_qr_ids_for_team(db, team_id)
    if registration:
        qr_ids.extend(await _collect_qr_ids_for_registration(db, registration.id))
    await _purge_qr_ids(db, list(dict.fromkeys(qr_ids)))

    await _purge_notifications(db, team_id=team_id, registration_id=registration.id if registration else None)

    if registration and registration.payment_id:
        await _purge_payment_row(db, registration.payment_id)

    await db.execute(delete(TeamInvitation).where(TeamInvitation.team_id == team_id))
    await db.execute(delete(TeamMember).where(TeamMember.team_id == team_id))

    # Registration.team_id FK must be cleared before teams row is removed.
    if registration:
        await db.execute(delete(Registration).where(Registration.id == registration.id))

    await db.execute(delete(Team).where(Team.id == team_id))


async def _delete_registration_tree(db: AsyncSession, registration: Registration) -> None:
    if registration.payment_id:
        _assert_payment_deletable(await _get_payment(db, registration.payment_id))

    if registration.team_id:
        await _purge_team_graph(db, registration.team_id, registration)
        return

    qr_ids = await _collect_qr_ids_for_registration(db, registration.id)
    await _purge_qr_ids(db, qr_ids)
    await _purge_notifications(db, registration_id=registration.id)
    if registration.payment_id:
        await _purge_payment_row(db, registration.payment_id)
    await db.execute(delete(Registration).where(Registration.id == registration.id))


async def admin_delete_registration(db: AsyncSession, registration_id: uuid.UUID, admin: AdminUser) -> dict:
    result = await db.execute(
        select(Registration).where(Registration.id == registration_id).with_for_update()
    )
    registration = result.scalar_one_or_none()
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registration not found")

    event_id = registration.event_id
    await _delete_registration_tree(db, registration)
    await db.commit()
    invalidate_spots_cache(event_id)
    invalidate_events_list_cache()
    _audit(admin, "ADMIN_REGISTRATION_DELETE", "registration", registration_id, event_id=str(event_id))
    return {"id": str(registration_id), "deleted": True}


async def admin_delete_team(db: AsyncSession, team_id: uuid.UUID, admin: AdminUser) -> dict:
    team_result = await db.execute(select(Team).where(Team.id == team_id).with_for_update())
    team = team_result.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    reg_result = await db.execute(select(Registration).where(Registration.team_id == team_id))
    registration = reg_result.scalar_one_or_none()

    if registration and registration.payment_id:
        _assert_payment_deletable(await _get_payment(db, registration.payment_id))

    event_id = team.event_id
    await _purge_team_graph(db, team_id, registration)
    await db.commit()
    invalidate_spots_cache(event_id)
    invalidate_events_list_cache()
    _audit(admin, "ADMIN_TEAM_DELETE", "team", team_id, event_id=str(event_id))
    return {"id": str(team_id), "deleted": True}


async def admin_delete_event(db: AsyncSession, event_id: uuid.UUID, admin: AdminUser) -> dict:
    event_result = await db.execute(select(Event).where(Event.id == event_id).with_for_update())
    event = event_result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    paid = await db.execute(
        select(Registration.id)
        .join(Payment, Payment.id == Registration.payment_id)
        .where(Registration.event_id == event_id, Payment.status.in_(tuple(_BLOCKED_PAYMENT)))
        .limit(1)
    )
    if paid.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete event while paid or refunded registrations exist",
        )

    regs_result = await db.execute(
        select(Registration).where(Registration.event_id == event_id).with_for_update()
    )
    for registration in regs_result.scalars().all():
        await _delete_registration_tree(db, registration)

    cp_result = await db.execute(select(AttendanceCheckpoint.id).where(AttendanceCheckpoint.event_id == event_id))
    cp_ids = [row[0] for row in cp_result.all()]
    if cp_ids:
        await db.execute(delete(AttendanceScan).where(AttendanceScan.checkpoint_id.in_(cp_ids)))
        await db.execute(delete(AttendanceCheckpoint).where(AttendanceCheckpoint.id.in_(cp_ids)))

    await db.execute(delete(EventRegistrationRule).where(EventRegistrationRule.event_id == event_id))
    await db.execute(delete(Event).where(Event.id == event_id))
    await db.commit()
    invalidate_spots_cache(event_id)
    invalidate_events_list_cache()
    _audit(admin, "ADMIN_EVENT_DELETE", "event", event_id)
    return {"id": str(event_id), "deleted": True}


async def admin_delete_admin_user(
    db: AsyncSession, admin_user_id: uuid.UUID, actor: AdminUser
) -> dict:
    if actor.id == admin_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own admin account",
        )

    result = await db.execute(
        select(AdminUser)
        .options(selectinload(AdminUser.role))
        .where(AdminUser.id == admin_user_id)
        .with_for_update()
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admin user not found")

    if target.role and target.role.name == SUPER_ADMIN_ROLE_NAME and target.is_active:
        others = await db.execute(
            select(func.count())
            .select_from(AdminUser)
            .join(Role, Role.id == AdminUser.role_id)
            .where(
                Role.name == SUPER_ADMIN_ROLE_NAME,
                AdminUser.is_active.is_(True),
                AdminUser.id != admin_user_id,
            )
        )
        if int(others.scalar_one()) == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete the last active SUPER ADMIN",
            )

    user_id = target.user_id
    await db.execute(delete(AdminUser).where(AdminUser.id == admin_user_id))
    await db.commit()
    invalidate_admin_cache(user_id)
    _audit(actor, "ADMIN_USER_DELETE", "admin_user", admin_user_id, target_user_id=str(user_id))
    return {"admin_user_id": str(admin_user_id), "deleted": True}


async def admin_delete_participant(db: AsyncSession, profile_id: uuid.UUID, admin: AdminUser) -> dict:
    result = await db.execute(select(Profile).where(Profile.id == profile_id).with_for_update())
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    active_reg = await db.execute(
        select(Registration.id)
        .where(Registration.profile_id == profile_id, Registration.status != RegistrationStatus.CANCELLED)
        .limit(1)
    )
    if active_reg.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active registrations block profile deletion",
        )

    active_member = await db.execute(
        select(TeamMember.id)
        .where(
            TeamMember.profile_id == profile_id,
            TeamMember.status.notin_(tuple(_TERMINAL_MEMBER)),
        )
        .limit(1)
    )
    if active_member.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Active team memberships block profile deletion",
        )

    paid = await db.execute(
        select(Payment.id)
        .where(Payment.payer_profile_id == profile_id, Payment.status.in_(tuple(_BLOCKED_PAYMENT)))
        .limit(1)
    )
    if paid.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Paid payment history blocks profile deletion",
        )

    await _purge_notifications(db, profile_id=profile_id)
    await db.execute(delete(Profile).where(Profile.id == profile_id))
    await db.commit()
    _audit(admin, "ADMIN_PARTICIPANT_DELETE", "profile", profile_id)
    return {"id": str(profile_id), "deleted": True}
