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
from app.payments.fulfillment import notify_payment_confirmed_safe, run_post_payment_fulfillment
from app.payments.reconciliation import log_payment_reconciliation, resolve_registration_for_payment
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


async def confirm_solo_registration(db: AsyncSession, registration: Registration) -> None:
    if registration.status == RegistrationStatus.CANCELLED:
        return
    await enforce_capacity_before_confirmation(db, registration)
    await db.execute(
        update(Registration)
        .where(Registration.id == registration.id)
        .values(status=RegistrationStatus.CONFIRMED)
    )


async def confirm_team_registration(
    db: AsyncSession, registration: Registration, payer_profile_id: UUID
) -> None:
    """Team registrations have team_id set and profile_id NULL (xor constraint)."""
    if not registration.team_id:
        raise RuntimeError(
            f"confirm_team_registration: registration {registration.id} is not a team registration"
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


async def _run_handoffs(
    db: AsyncSession, payment: Payment, registration: Optional[Registration]
) -> None:
    await run_post_payment_fulfillment(
        db,
        payment=payment,
        registration=registration,
        send_notification=False,
    )


async def _confirm_registration_for_payment(
    db: AsyncSession, payment: Payment
) -> Optional[Registration]:
    registration = await resolve_registration_for_payment(db, payment)
    log_payment_reconciliation("confirm_registration", payment=payment, registration=registration)

    if registration is None:
        logger.warning(
            "payment_success_without_registration_link payment_id=%s payment_status=%s",
            payment.id,
            payment.status.value,
        )
        return None

    if registration.status == RegistrationStatus.CANCELLED:
        logger.warning(
            "payment_success_registration_cancelled payment_id=%s registration_id=%s",
            payment.id,
            registration.id,
        )
        return registration

    if registration.payment_id != payment.id:
        registration.payment_id = payment.id
        await db.flush()

    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        await confirm_solo_registration(db, registration)
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        await confirm_team_registration(db, registration, payment.payer_profile_id)
    return registration


async def apply_payment_success(
    db: AsyncSession, payment_id: UUID, razorpay_payment_id: str
) -> ApplyResult:
    payment = await _load_payment_for_update(db, payment_id)
    if payment is None:
        return ApplyResult(applied=False, payment=None)

    registration = await resolve_registration_for_payment(db, payment)
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
        registration_confirmed = await _confirm_registration_for_payment(db, payment)
        await _run_handoffs(db, payment, registration_confirmed)
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
        await notify_payment_confirmed_safe(db, row.id)

    refreshed = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment_after = refreshed.scalar_one_or_none()
    registration_after = (
        await resolve_registration_for_payment(db, payment_after) if payment_after else None
    )
    if registration_after is not None:
        from app.services.google_sheets_triggers import trigger_registration_sheet_sync

        trigger_registration_sheet_sync(registration_after.id)

    return ApplyResult(applied=newly_paid, payment=row)


def _apply_provider_metadata(payment: Payment, metadata: dict) -> None:
    for field in (
        "provider_error_code",
        "provider_error_description",
        "provider_error_source",
        "provider_error_step",
        "provider_payment_method",
        "last_provider_event_at",
    ):
        value = metadata.get(field)
        if value is not None:
            setattr(payment, field, value)


async def apply_payment_failure(db: AsyncSession, payment_id: UUID, **metadata) -> ApplyResult:
    payment = await _load_payment_for_update(db, payment_id)
    if payment is None:
        return ApplyResult(applied=False, payment=None)
    if payment.status in (PaymentStatus.PAID, PaymentStatus.REFUNDED):
        return ApplyResult(applied=False, payment=_as_row(payment))
    if payment.status == PaymentStatus.FAILED:
        _apply_provider_metadata(payment, metadata)
        await db.flush()
        await db.commit()
        return ApplyResult(applied=False, payment=_as_row(payment))

    payment = await transition_payment_status(
        db,
        payment_id,
        [PaymentStatus.CREATED],
        status=PaymentStatus.FAILED,
    )
    if payment is None:
        payment = await _load_payment_for_update(db, payment_id)
        if payment is None:
            return ApplyResult(applied=False, payment=None)
        _apply_provider_metadata(payment, metadata)
        await db.flush()
        await db.commit()
        return ApplyResult(applied=False, payment=_as_row(payment))
    _apply_provider_metadata(payment, metadata)
    await db.flush()
    await db.commit()
    return ApplyResult(applied=True, payment=_as_row(payment))
