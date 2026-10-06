"""Idempotent payment status transitions via async SQLAlchemy."""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus, TeamMemberRole, TeamMemberStatus, TeamStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.payments.handoffs import issue_receipt, trigger_qr
from app.payments.reconciliation import log_payment_reconciliation, registration_linked_to_payment
from app.services import notification_service
from app.services.event_service import enforce_capacity_before_confirmation

logger = logging.getLogger("payments.apply")


@dataclass
class PaymentRow:
    id: UUID
    payment_type: PaymentType
    team_member_id: Optional[UUID]
    payer_profile_id: UUID
    amount_paise: int


@dataclass
class ApplyResult:
    applied: bool
    payment: Optional[PaymentRow]


def _as_row(payment: Payment) -> PaymentRow:
    return PaymentRow(
        id=payment.id,
        payment_type=payment.payment_type,
        team_member_id=payment.team_member_id,
        payer_profile_id=payment.payer_profile_id,
        amount_paise=payment.amount_paise,
    )


async def _load_payment_for_update(db: AsyncSession, payment_id: UUID) -> Optional[Payment]:
    result = await db.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
    return result.scalar_one_or_none()


async def transition_payment_status(
    db: AsyncSession,
    payment_id: UUID,
    from_statuses: list[PaymentStatus],
    *,
    status: PaymentStatus,
    razorpay_payment_id: Optional[str] = None,
) -> Optional[Payment]:
    values: dict = {
        "status": status,
        "updated_at": datetime.now(timezone.utc),
    }
    if razorpay_payment_id is not None:
        values["razorpay_payment_id"] = razorpay_payment_id

    result = await db.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status.in_(from_statuses))
        .values(**values)
        .returning(Payment)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return None
    await db.flush()
    return payment


async def confirm_solo_registration(db: AsyncSession, payment_id: UUID) -> None:
    reg_result = await db.execute(
        select(Registration).where(
            Registration.payment_id == payment_id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
    )
    registration = reg_result.scalar_one_or_none()
    if registration is None:
        return
    await enforce_capacity_before_confirmation(db, registration)
    await db.execute(
        update(Registration)
        .where(Registration.id == registration.id)
        .values(status=RegistrationStatus.CONFIRMED)
    )


async def _resolve_team_registration(
    db: AsyncSession, payment_id: UUID, payer_profile_id: UUID
) -> Optional[Registration]:
    """Find the team registration for a TEAM_REGISTRATION payment."""
    linked = await db.execute(select(Registration).where(Registration.payment_id == payment_id))
    registration = linked.scalar_one_or_none()
    if registration and registration.team_id:
        return registration

    pending_result = await db.execute(
        select(Registration)
        .join(Team, Registration.team_id == Team.id)
        .where(
            Team.leader_profile_id == payer_profile_id,
            Registration.team_id.isnot(None),
            Registration.status == RegistrationStatus.PENDING,
            Registration.payment_id.is_(None),
        )
    )
    pending = list(pending_result.scalars().all())
    if len(pending) == 1:
        registration = pending[0]
        registration.payment_id = payment_id
        await db.flush()
        logger.warning(
            "team_registration_payment_link_recovered payment_id=%s registration_id=%s team_id=%s event_id=%s",
            payment_id,
            registration.id,
            registration.team_id,
            registration.event_id,
        )
        return registration

    if len(pending) > 1:
        logger.error(
            "team_registration_payment_link_ambiguous payment_id=%s payer_profile_id=%s candidate_count=%s",
            payment_id,
            payer_profile_id,
            len(pending),
        )
    return None


async def confirm_team_registration(db: AsyncSession, payment_id: UUID, payer_profile_id: UUID) -> None:
    """Team registrations have team_id set and profile_id NULL (xor constraint)."""
    registration = await _resolve_team_registration(db, payment_id, payer_profile_id)
    if not registration or not registration.team_id:
        logger.error(
            "team_registration_payment_unlinked payment_id=%s payer_profile_id=%s",
            payment_id,
            payer_profile_id,
        )
        raise RuntimeError(
            f"confirm_team_registration: no team registration linked to payment {payment_id}"
        )

    if registration.status == RegistrationStatus.CANCELLED:
        logger.warning(
            "team_registration_confirm_skipped_cancelled payment_id=%s registration_id=%s",
            payment_id,
            registration.id,
        )
        return

    await enforce_capacity_before_confirmation(db, registration)

    await db.execute(
        update(Team).where(Team.id == registration.team_id).values(status=TeamStatus.PAID)
    )
    await db.execute(
        update(TeamMember)
        .where(
            TeamMember.team_id == registration.team_id,
            TeamMember.role == TeamMemberRole.LEADER,
            TeamMember.profile_id == payer_profile_id,
        )
        .values(status=TeamMemberStatus.ACTIVE)
    )
    await db.execute(
        update(Registration)
        .where(
            Registration.id == registration.id,
            Registration.status != RegistrationStatus.CANCELLED,
        )
        .values(status=RegistrationStatus.CONFIRMED)
    )


async def _run_handoffs(db: AsyncSession, payment: PaymentRow) -> None:
    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        reg_result = await db.execute(select(Registration).where(Registration.payment_id == payment.id))
        registration = reg_result.scalar_one_or_none()
        if registration is not None:
            await trigger_qr(db, registration_id=registration.id)
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        reg_result = await db.execute(select(Registration).where(Registration.payment_id == payment.id))
        registration = reg_result.scalar_one_or_none()
        if registration is not None and registration.team_id is not None:
            members_result = await db.execute(
                select(TeamMember).where(
                    TeamMember.team_id == registration.team_id,
                    TeamMember.status == TeamMemberStatus.ACTIVE,
                )
            )
            for member in members_result.scalars().all():
                await trigger_qr(db, team_member_id=member.id)

    await issue_receipt(db, payment.id)


async def _confirm_registration_for_payment(db: AsyncSession, payment: Payment) -> None:
    registration = await registration_linked_to_payment(db, payment.id)
    log_payment_reconciliation("confirm_registration", payment=payment, registration=registration)

    if registration is None:
        logger.warning(
            "payment_success_without_registration_link payment_id=%s payment_status=%s",
            payment.id,
            payment.status.value,
        )
        return

    if registration.status == RegistrationStatus.CANCELLED:
        logger.warning(
            "payment_success_registration_cancelled payment_id=%s registration_id=%s",
            payment.id,
            registration.id,
        )
        return

    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        await confirm_solo_registration(db, payment.id)
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        await confirm_team_registration(db, payment.id, payment.payer_profile_id)


async def apply_payment_success(
    db: AsyncSession, payment_id: UUID, razorpay_payment_id: str
) -> ApplyResult:
    payment = await _load_payment_for_update(db, payment_id)
    if payment is None:
        return ApplyResult(applied=False, payment=None)

    registration = await registration_linked_to_payment(db, payment.id)
    log_payment_reconciliation(
        "apply_payment_success_start",
        payment=payment,
        registration=registration,
        razorpay_payment_id=razorpay_payment_id,
    )

    newly_paid = False
    if payment.status == PaymentStatus.PAID:
        if payment.razorpay_payment_id is None and razorpay_payment_id:
            payment.razorpay_payment_id = razorpay_payment_id
            await db.flush()
    elif payment.status in (PaymentStatus.CREATED, PaymentStatus.FAILED):
        transitioned = await transition_payment_status(
            db,
            payment_id,
            [PaymentStatus.CREATED, PaymentStatus.FAILED],
            status=PaymentStatus.PAID,
            razorpay_payment_id=razorpay_payment_id,
        )
        if transitioned is None:
            payment = await _load_payment_for_update(db, payment_id)
            if payment is None or payment.status != PaymentStatus.PAID:
                return ApplyResult(applied=False, payment=None)
        else:
            payment = transitioned
            newly_paid = True
    else:
        return ApplyResult(applied=False, payment=_as_row(payment))

    row = _as_row(payment)
    try:
        await _confirm_registration_for_payment(db, payment)
        await _run_handoffs(db, row)
    except Exception:
        logger.exception(
            "payment_post_processing_failed payment_id=%s registration_id=%s",
            payment.id,
            registration.id if registration else None,
        )
        raise

    await db.commit()

    from app.services.admin_ops_service import invalidate_dashboard_cache
    from app.services.event_service import invalidate_spots_cache

    invalidate_spots_cache()
    invalidate_dashboard_cache()

    if newly_paid:
        await notification_service.notify_payment_confirmed(db, row.id)
    return ApplyResult(applied=newly_paid, payment=row)


async def apply_payment_failure(db: AsyncSession, payment_id: UUID) -> ApplyResult:
    payment = await transition_payment_status(
        db,
        payment_id,
        [PaymentStatus.CREATED],
        status=PaymentStatus.FAILED,
    )
    if payment is None:
        return ApplyResult(applied=False, payment=None)
    await db.commit()
    return ApplyResult(applied=True, payment=_as_row(payment))
