import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_deps import require_admin_user, require_super_admin_user
from app.db.session import get_db
from app.models.enums import PaymentStatus, PaymentType
from app.models.payment import Payment
from app.models.user import User
from app.schemas.payment import PaymentListResponse, PaymentOut, RefundRequest, RefundResponse
from app.services.refund import RefundError, refund_payment

router = APIRouter(prefix="/admin", tags=["Admin Payments"])


# Search/filter by participant, event, team, payment ID, Razorpay IDs, type,
# status. Participant filtering here is by payer_profile_id directly.
# Event/team filtering would join through registrations/teams, which this
# module does not own, so those params are not wired up yet — a TODO once
# registrations/teams expose a stable join key.
@router.get("/payments", response_model=PaymentListResponse)
async def list_payments(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin_user),
    payment_id: Optional[uuid.UUID] = None,
    participant_id: Optional[uuid.UUID] = None,
    razorpay_order_id: Optional[str] = None,
    razorpay_payment_id: Optional[str] = None,
    type: Optional[PaymentType] = None,
    status_filter: Optional[PaymentStatus] = None,
    page: int = 1,
    page_size: int = 25,
):
    page = max(1, page)
    page_size = min(100, max(1, page_size))

    query = select(Payment)
    if payment_id:
        query = query.where(Payment.id == payment_id)
    if participant_id:
        query = query.where(Payment.payer_profile_id == participant_id)
    if razorpay_order_id:
        query = query.where(Payment.razorpay_order_id == razorpay_order_id)
    if razorpay_payment_id:
        query = query.where(Payment.razorpay_payment_id == razorpay_payment_id)
    if type:
        query = query.where(Payment.payment_type == type)
    if status_filter:
        query = query.where(Payment.status == status_filter)

    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar_one()

    start = (page - 1) * page_size
    result = await db.execute(query.order_by(Payment.created_at.desc()).offset(start).limit(page_size))
    payments = list(result.scalars().all())

    return PaymentListResponse(
        payments=[PaymentOut.model_validate(p) for p in payments], page=page, page_size=page_size, total=total
    )


@router.post("/payments/{payment_id}/refund", response_model=RefundResponse)
async def refund(
    payment_id: uuid.UUID,
    body: RefundRequest,
    db: AsyncSession = Depends(get_db),
    _super_admin: User = Depends(require_super_admin_user),
):
    try:
        result = await refund_payment(db, payment_id, body.reason)
    except RefundError as err:
        raise HTTPException(status_code=err.status, detail=str(err))
    return RefundResponse(
        payment_id=result.payment_id, refund_id=result.refund_id, refund_amount_paise=result.refund_amount_paise
    )
