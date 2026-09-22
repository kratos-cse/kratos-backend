import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile, get_current_user
from app.db.session import get_db
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.receipt import Receipt
from app.models.team import TeamMember
from app.models.user import User
from app.schemas.registration import QRCodeOut, ReceiptOut, RegistrationCreateRequest, RegistrationOut
from app.services.registration_service import (
    assert_can_view_registration,
    cancel_unpaid_registration,
    create_registration,
    get_registration_or_404,
    list_my_registrations,
)

router = APIRouter(tags=["Registration"])


@router.post(
    "/events/{event_id}/registrations",
    response_model=RegistrationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_event_registration(
    event_id: uuid.UUID,
    payload: RegistrationCreateRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    """
    Create a solo registration, or initialize a team registration (creates
    the TEAM + leader TEAM_MEMBER row too). Does NOT create a payment —
    call POST /payments/create-order next (Payments feature branch) with
    the returned registration/team id.
    """
    return await create_registration(db, event_id, profile, payload)


@router.get("/registrations/{registration_id}", response_model=RegistrationOut)
async def get_registration(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    registration = await get_registration_or_404(db, registration_id)
    await assert_can_view_registration(db, registration, profile, current_user.is_admin_flagged)
    return registration


@router.post("/registrations/{registration_id}/cancel", response_model=RegistrationOut)
async def cancel_registration(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    """
    Cancel an unpaid registration. Allows participants (or team leaders) to
    cancel a pending registration and free up their slot so they can re-register.
    """
    return await cancel_unpaid_registration(
        db,
        registration_id=registration_id,
        profile=profile,
        is_admin=current_user.is_admin_flagged,
    )



@router.get("/users/me/registrations", response_model=list[RegistrationOut])
async def list_own_registrations(
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await list_my_registrations(db, profile)


@router.get("/registrations/{registration_id}/receipt", response_model=ReceiptOut)
async def get_registration_receipt(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    registration = await get_registration_or_404(db, registration_id)
    await assert_can_view_registration(db, registration, profile, current_user.is_admin_flagged)

    if registration.payment_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No payment recorded for this registration yet"
        )

    result = await db.execute(select(Receipt).where(Receipt.payment_id == registration.payment_id))
    receipt = result.scalar_one_or_none()
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt has not been generated yet")
    return receipt


@router.get("/registrations/{registration_id}/qr", response_model=QRCodeOut)
async def get_registration_qr(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    registration = await get_registration_or_404(db, registration_id)
    await assert_can_view_registration(db, registration, profile, current_user.is_admin_flagged)

    qr = None
    if registration.profile_id is not None:
        result = await db.execute(select(QRCode).where(QRCode.registration_id == registration.id))
        qr = result.scalar_one_or_none()
    elif registration.team_id is not None:
        member_result = await db.execute(
            select(TeamMember).where(
                TeamMember.team_id == registration.team_id, TeamMember.profile_id == profile.id
            )
        )
        member = member_result.scalar_one_or_none()
        if member is not None:
            qr_result = await db.execute(select(QRCode).where(QRCode.team_member_id == member.id))
            qr = qr_result.scalar_one_or_none()

    if qr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="QR code has not been generated yet")
    return qr
