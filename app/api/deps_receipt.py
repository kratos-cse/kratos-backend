"""Receipt endpoint authentication (Bearer session OR receipt-scoped token)."""
import uuid

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.receipt_token import decode_receipt_access_token
from app.core.security import bearer_scheme, decode_token
from app.db.session import get_db
from app.models.admin import AdminUser
from app.models.payment import Payment
from app.models.profile import Profile
from app.models.registration import Registration
from app.models.user import User
from app.services.registration_service import assert_can_view_registration


async def _profile_from_bearer(db: AsyncSession, credentials: HTTPAuthorizationCredentials) -> Profile:
    payload = decode_token(credentials.credentials)
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    result = await db.execute(select(Profile).where(Profile.user_id == user.id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found for this user")
    return profile


async def assert_can_access_payment(db: AsyncSession, payment: Payment, payer: Profile) -> None:
    if payment.payer_profile_id == payer.id:
        return
    admin_result = await db.execute(
        select(AdminUser).where(AdminUser.user_id == payer.user_id, AdminUser.is_active.is_(True))
    )
    if admin_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your payment")


async def assert_can_access_receipt(db: AsyncSession, payment: Payment, profile: Profile) -> None:
    """Payer, admin, or anyone who may view the linked registration may access receipts."""
    if payment.payer_profile_id == profile.id:
        return
    admin_result = await db.execute(
        select(AdminUser).where(AdminUser.user_id == profile.user_id, AdminUser.is_active.is_(True))
    )
    if admin_result.scalar_one_or_none() is not None:
        return

    reg_result = await db.execute(select(Registration).where(Registration.payment_id == payment.id))
    registration = reg_result.scalar_one_or_none()
    if registration is not None:
        await assert_can_view_registration(db, registration, profile, is_admin=False)
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your payment")


async def authorize_payment_receipt_access(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    receipt_token: str | None = Query(default=None, alias="receipt_token"),
) -> Profile:
    """
    Authorize receipt viewing for a payment.
    Accepts either Authorization Bearer (session JWT) or ?receipt_token= (scoped).
    """
    if receipt_token:
        profile_id = decode_receipt_access_token(receipt_token, payment_id)
        result = await db.execute(select(Profile).where(Profile.id == profile_id))
        profile = result.scalar_one_or_none()
        if profile is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid receipt token")
        result = await db.execute(select(Payment).where(Payment.id == payment_id))
        payment = result.scalar_one_or_none()
        if payment is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
        await assert_can_access_receipt(db, payment, profile)
        return profile

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    profile = await _profile_from_bearer(db, credentials)
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    await assert_can_access_receipt(db, payment, profile)
    return profile
