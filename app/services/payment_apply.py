"""The ONLY place payment status changes. /verify and /webhook both call
apply_payment_success/apply_payment_failure — never duplicate this
transition logic in a route handler.

Idempotency without a separate dedup table (there isn't one in the schema):
a conditional UPDATE ... WHERE status IN (...) RETURNING *, so a retry that
hits a payment no longer in one of those statuses affects zero rows and is
a no-op. Guard is CREATED|FAILED, not "!= PAID": that stops a late-arriving
retry from flipping an already-REFUNDED payment back to PAID, while still
allowing the real case where payment.failed lands first and the user
successfully retries on the same order.

Each call is one transaction: the status flip and the linked
registration/team-member write commit together, so a crash or exception
between them leaves the payment CREATED/FAILED (safe to retry) rather than
PAID with side effects half-applied.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PaymentStatus, PaymentType, RegistrationStatus, TeamMemberStatus, TeamStatus
from app.models.payment import Payment
from app.models.registration import Registration
from app.models.team import Team, TeamMember

logger = logging.getLogger("payments.apply")


@dataclass
class ApplyResult:
    applied: bool
    payment: Optional[Payment]


class PaymentsStore(Protocol):
    """Narrow seam between the state-transition logic below and actual
    storage, so apply_payment_success/failure can be driven by a small
    in-memory stub in tests without a real database session."""

    async def transition_payment_status(
        self, payment_id: uuid.UUID, from_statuses: list[PaymentStatus], patch: dict
    ) -> Optional[Payment]: ...

    async def confirm_solo_registration(self, payment_id: uuid.UUID) -> None: ...

    async def confirm_team_registration(self, payment_id: uuid.UUID) -> None: ...

    async def confirm_team_member_topup(self, team_member_id: uuid.UUID) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class SqlAlchemyPaymentsStore:
    """Real store used by every route, backed by the request's AsyncSession."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def transition_payment_status(
        self, payment_id: uuid.UUID, from_statuses: list[PaymentStatus], patch: dict
    ) -> Optional[Payment]:
        result = await self.db.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status.in_(from_statuses))
            .values(**patch)
            .returning(Payment)
        )
        return result.scalar_one_or_none()

    async def confirm_solo_registration(self, payment_id: uuid.UUID) -> None:
        await self.db.execute(
            update(Registration).where(Registration.payment_id == payment_id).values(status=RegistrationStatus.CONFIRMED)
        )

    async def confirm_team_registration(self, payment_id: uuid.UUID) -> None:
        result = await self.db.execute(select(Registration.team_id).where(Registration.payment_id == payment_id))
        team_id = result.scalar_one_or_none()
        if team_id is None:
            raise RuntimeError(f"confirm_team_registration: no registration found for payment {payment_id}")

        team_result = await self.db.execute(select(Team).where(Team.id == team_id))
        team = team_result.scalar_one_or_none()
        if team is None:
            raise RuntimeError(f"confirm_team_registration: team {team_id} not found")

        team.status = TeamStatus.PAID
        await self.db.execute(
            update(TeamMember)
            .where(TeamMember.team_id == team_id, TeamMember.profile_id == team.leader_profile_id)
            .values(status=TeamMemberStatus.ACTIVE)
        )

    async def confirm_team_member_topup(self, team_member_id: uuid.UUID) -> None:
        await self.db.execute(
            update(TeamMember).where(TeamMember.id == team_member_id).values(status=TeamMemberStatus.ACTIVE)
        )

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()


def _run_handoffs(payment: Payment) -> None:
    from app.services.handoffs import issue_receipt, send_notification, trigger_qr

    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        trigger_qr(registration_id=payment.id)
    elif payment.payment_type == PaymentType.TEAM_MEMBER_TOPUP and payment.team_member_id:
        trigger_qr(team_member_id=payment.team_member_id)
    # TEAM_REGISTRATION does not issue a QR by itself — joining members get theirs
    # on their own TEAM_MEMBER_TOPUP or free-join confirmation (see brief §4).

    send_notification(kind="PAYMENT_CONFIRMED", payer_profile_id=payment.payer_profile_id, amount_paise=payment.amount_paise)
    issue_receipt(payment_id=payment.id)


async def apply_payment_success(
    store: PaymentsStore, payment_id: uuid.UUID, razorpay_payment_id: str
) -> ApplyResult:
    payment = await store.transition_payment_status(
        payment_id,
        [PaymentStatus.CREATED, PaymentStatus.FAILED],
        {"status": PaymentStatus.PAID, "razorpay_payment_id": razorpay_payment_id},
    )
    if payment is None:
        await store.rollback()
        return ApplyResult(applied=False, payment=None)

    if payment.payment_type == PaymentType.SOLO_REGISTRATION:
        await store.confirm_solo_registration(payment.id)
    elif payment.payment_type == PaymentType.TEAM_REGISTRATION:
        await store.confirm_team_registration(payment.id)
    elif payment.payment_type == PaymentType.TEAM_MEMBER_TOPUP:
        if not payment.team_member_id:
            raise RuntimeError(f"TEAM_MEMBER_TOPUP payment {payment.id} is missing team_member_id")
        await store.confirm_team_member_topup(payment.team_member_id)

    await store.commit()
    _run_handoffs(payment)
    return ApplyResult(applied=True, payment=payment)


async def apply_payment_failure(store: PaymentsStore, payment_id: uuid.UUID) -> ApplyResult:
    # Only CREATED -> FAILED. A PAID or REFUNDED payment must never be moved to
    # FAILED by a stray/duplicate failure webhook.
    payment = await store.transition_payment_status(payment_id, [PaymentStatus.CREATED], {"status": PaymentStatus.FAILED})
    if payment is None:
        await store.rollback()
        return ApplyResult(applied=False, payment=None)
    await store.commit()
    return ApplyResult(applied=True, payment=payment)
