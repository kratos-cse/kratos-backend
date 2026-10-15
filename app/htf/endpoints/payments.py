import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile
from app.db.session import get_db
from app.htf.schemas.payment import PaymentOrderOut, PaymentVerifyRequest, PaymentVerifyOut
from app.htf.services import payment_service
from app.models.profile import Profile

router = APIRouter(tags=["HTF Payments"])


@router.post(
    "/htf/applications/{application_id}/payment/order",
    response_model=PaymentOrderOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_htf_payment_order(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
) -> PaymentOrderOut:
    return await payment_service.create_payment_order(
        db,
        application_id=application_id,
        profile=profile,
    )


@router.post(
    "/htf/payments/verify",
    response_model=PaymentVerifyOut,
    status_code=status.HTTP_200_OK,
)
async def verify_htf_payment(
    payload: PaymentVerifyRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
) -> PaymentVerifyOut:
    res = await payment_service.verify_payment(
        db,
        razorpay_order_id=payload.razorpay_order_id,
        razorpay_payment_id=payload.razorpay_payment_id,
        razorpay_signature=payload.razorpay_signature,
        profile=profile,
    )
    return PaymentVerifyOut(**res)


@router.post(
    "/htf/payments/webhook",
    status_code=status.HTTP_200_OK,
)
async def htf_payment_webhook(
    request: Request,
    x_razorpay_signature: Optional[str] = Header(None, alias="X-Razorpay-Signature"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw_body = await request.body()
    return await payment_service.handle_webhook(
        db,
        raw_body=raw_body,
        signature=x_razorpay_signature,
    )


@router.post(
    "/htf/applications/{application_id}/payment/sync",
    status_code=status.HTTP_200_OK,
)
async def sync_htf_payment(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
) -> dict:
    return await payment_service.sync_payment(
        db,
        application_id=application_id,
        profile=profile,
    )
