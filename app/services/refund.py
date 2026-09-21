"""§6 refund flow: Razorpay refund -> update payments -> linked-entity state ->
notification, in that order. The route handler is responsible for the
admin authorization check — this function assumes it has already been done.

Row-level locking (`with_for_update`) ensures concurrent refund calls queue
and serialize, preventing duplicate remote refunds at the payment gateway.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus, TeamMemberStatus, TeamStatus
from app.models.external_mirrors import Registration, Team, TeamMember
from app.models.payment import Payment
from app.services.handoffs import send_notification
from app.services.razorpay_client import get_razorpay


class RefundError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class RefundResult:
    payment_id: uuid.UUID
    refund_id: str
    refund_amount_paise: int


def refund_payment(db: Session, payment_id: uuid.UUID, reason: str) -> RefundResult:
    # Lock row to prevent concurrent duplicate remote refund calls
    payment = db.execute(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    ).scalar_one_or_none()

    if payment is None:
        raise RefundError(f"Payment {payment_id} not found", status=404)
    if payment.status != PaymentStatus.PAID.value:
        raise RefundError(f"Payment {payment_id} is not PAID (status={payment.status})", status=409)
    if not payment.razorpay_payment_id:
        raise RefundError(f"Payment {payment_id} has no razorpay_payment_id to refund", status=409)

    # 1. Razorpay refund (invoked directly without blind retry).
    try:
        refund = get_razorpay().payment.refund(
            payment.razorpay_payment_id, {"amount": payment.amount_paise}
        )
    except Exception as err:
        db.rollback()
        raise RefundError(f"Razorpay refund failed: {err}", status=502)

    # 2. Update payments row.
    updated_payment = db.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == PaymentStatus.PAID.value)
        .values(
            status=PaymentStatus.REFUNDED.value,
            refund_id=refund["id"],
            refund_amount_paise=int(refund["amount"]),
            refunded_at=datetime.now(timezone.utc),
            refund_reason=reason,
        )
        .returning(Payment)
    ).scalar_one_or_none()

    if updated_payment is None:
        db.rollback()
        raise RefundError(
            f"Razorpay refund {refund['id']} succeeded but payments row update failed for {payment_id} — "
            "reconcile manually",
            status=500,
        )

    # 3. Linked registration/team-member state.
    if payment.payment_type == PaymentType.SOLO_REGISTRATION.value:
        db.execute(
            update(Registration).where(Registration.payment_id == payment_id).values(status=RegistrationStatus.CANCELLED.value)
        )
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION.value:
        team_id = db.execute(select(Registration.team_id).where(Registration.payment_id == payment_id)).scalar_one_or_none()
        if team_id is not None:
            db.execute(update(Team).where(Team.id == team_id).values(status=TeamStatus.CANCELLED.value))
    elif payment.payment_type == PaymentType.TEAM_MEMBER_TOPUP.value and payment.team_member_id:
        db.execute(
            update(TeamMember).where(TeamMember.id == payment.team_member_id).values(status=TeamMemberStatus.REMOVED.value)
        )

    db.commit()

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
