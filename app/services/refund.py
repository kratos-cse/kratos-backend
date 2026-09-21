"""§6 refund flow: Razorpay refund -> update payments -> linked-entity state ->
notification, in that order. The route handler is responsible for the
admin authorization check — this function assumes it has already been done.

Linked-entity states are grounded in the real enums (app/models/enums.py,
confirmed against alembic/versions/0001_initial_schema.py), not guessed:
RegistrationStatus.CANCELLED, TeamStatus.CANCELLED, TeamMemberStatus.REMOVED
all exist exactly as used below. What's still worth a one-line confirmation
with the registrations/teams owners before this ships is the *cascading*
behavior for a TEAM_REGISTRATION refund — this only flips the team's own
status, it does not also touch any members who are already ACTIVE.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus, TeamMemberStatus, TeamStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import Team, TeamMember
from app.services.handoffs import send_notification
from app.services.razorpay_client import get_razorpay, with_retry


class RefundError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class RefundResult:
    payment_id: uuid.UUID
    refund_id: str
    refund_amount_paise: int


async def refund_payment(db: AsyncSession, payment_id: uuid.UUID, reason: str) -> RefundResult:
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise RefundError(f"Payment {payment_id} not found", status=404)
    if payment.status != PaymentStatus.PAID:
        raise RefundError(f"Payment {payment_id} is not PAID (status={payment.status})", status=409)
    if not payment.razorpay_payment_id:
        raise RefundError(f"Payment {payment_id} has no razorpay_payment_id to refund", status=409)

    # 1. Razorpay refund.
    razorpay_payment_id = payment.razorpay_payment_id
    amount_paise = payment.amount_paise
    refund = await with_retry(lambda: get_razorpay().payment.refund(razorpay_payment_id, {"amount": amount_paise}))

    # 2. payments row. Conditional on still being PAID, mirroring the idempotency
    # guard in payment_apply.py — a duplicate refund click cannot double-apply.
    update_result = await db.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PAID)
        .values(
            status=PaymentStatus.REFUNDED,
            refund_id=refund["id"],
            refund_amount_paise=int(refund["amount"]),
            refunded_at=datetime.now(timezone.utc),
            refund_reason=reason,
        )
        .returning(Payment)
    )
    updated_payment = update_result.scalar_one_or_none()
    if updated_payment is None:
        await db.rollback()
        # The Razorpay refund already succeeded even though our row didn't flip (a
        # concurrent refund attempt beat us to it) — surface this loudly rather than
        # silently swallowing a real refund that isn't reflected in our records.
        raise RefundError(
            f"Razorpay refund {refund['id']} succeeded but payments row update failed for {payment_id} — "
            "reconcile manually",
            status=500,
        )

    # 3. Linked registration/team-member state.
    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        await db.execute(
            update(Registration).where(Registration.payment_id == payment_id).values(status=RegistrationStatus.CANCELLED)
        )
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        reg_result = await db.execute(select(Registration.team_id).where(Registration.payment_id == payment_id))
        team_id = reg_result.scalar_one_or_none()
        if team_id is not None:
            await db.execute(update(Team).where(Team.id == team_id).values(status=TeamStatus.CANCELLED))
    elif payment.payment_type == PaymentType.TEAM_MEMBER_TOPUP and payment.team_member_id:
        await db.execute(
            update(TeamMember).where(TeamMember.id == payment.team_member_id).values(status=TeamMemberStatus.REMOVED)
        )

    await db.commit()

    # 4. Notification handoff.
    send_notification(
        kind="REFUND_ISSUED",
        payer_profile_id=payment.payer_profile_id,
        amount_paise=int(refund["amount"]),
        reason=reason,
    )

    return RefundResult(
        payment_id=updated_payment.id, refund_id=refund["id"], refund_amount_paise=int(refund["amount"])
    )
