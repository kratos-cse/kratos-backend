import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps_receipt import authorize_payment_receipt_access
from app.core.receipt_token import create_receipt_access_token
from app.core.security import bearer_scheme, get_current_profile, get_current_user
from app.db.session import get_db
from app.models.enums import PaymentStatus, RegistrationStatus
from app.models.profile import Profile
from app.models.qr_code import QRCode
from app.models.team import TeamMember
from app.models.user import User
from app.schemas.registration import (
    QRCodeOut,
    ReceiptAccessTokenOut,
    ReceiptOut,
    RegistrationCreateRequest,
    RegistrationOut,
)
from app.services.receipt_service import (
    ensure_receipt,
    get_receipt_data_context,
    receipt_html_url,
    receipt_pdf_url,
    render_html_receipt,
)
from app.services.registration_service import (
    assert_can_view_registration,
    cancel_unpaid_registration,
    create_registration,
    get_registration_or_404,
    list_my_registrations,
)

router = APIRouter(tags=["Registration"])


def _receipt_out(receipt, payment_id: uuid.UUID) -> ReceiptOut:
    return ReceiptOut(
        id=receipt.id,
        receipt_number=receipt.receipt_number,
        pdf_url=receipt_pdf_url(payment_id),
        html_url=receipt_html_url(payment_id),
        issued_at=receipt.issued_at,
    )


async def _assert_paid_receipt(registration) -> None:
    if registration.payment_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No payment recorded for this registration yet",
        )
    payment = registration.payment
    if payment is None or payment.status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt is only available after payment is confirmed",
        )
    if registration.status != RegistrationStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Receipt is only available for confirmed registrations",
        )


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

    if registration.payment and registration.payment.status == PaymentStatus.CREATED:
        from app.api.v1.endpoints.payments import _sync_payment_if_needed

        await _sync_payment_if_needed(db, registration.payment)
        registration = await get_registration_or_404(db, registration_id)

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
    await _assert_paid_receipt(registration)

    receipt = await ensure_receipt(db, registration.payment_id)
    await db.commit()
    return _receipt_out(receipt, registration.payment_id)


@router.post("/registrations/{registration_id}/receipt/access-token", response_model=ReceiptAccessTokenOut)
async def issue_registration_receipt_access_token(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    profile: Profile = Depends(get_current_profile),
):
    registration = await get_registration_or_404(db, registration_id)
    await assert_can_view_registration(db, registration, profile, current_user.is_admin_flagged)
    await _assert_paid_receipt(registration)

    payment_id = registration.payment_id
    await ensure_receipt(db, payment_id)
    await db.commit()

    access_token, expires_in = create_receipt_access_token(payment_id, profile.id)
    return ReceiptAccessTokenOut(
        access_token=access_token,
        expires_in=expires_in,
        html_url=receipt_html_url(payment_id, access_token),
        pdf_url=receipt_pdf_url(payment_id, access_token),
    )


@router.get("/registrations/{registration_id}/receipt/html", response_class=HTMLResponse)
async def get_registration_receipt_html(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    receipt_token: str | None = Query(default=None, alias="receipt_token"),
):
    registration = await get_registration_or_404(db, registration_id)
    await _assert_paid_receipt(registration)

    await authorize_payment_receipt_access(
        registration.payment_id,
        db=db,
        credentials=credentials,
        receipt_token=receipt_token,
    )

    await ensure_receipt(db, registration.payment_id)
    await db.commit()

    ctx = await get_receipt_data_context(db, registration.payment_id)
    return HTMLResponse(content=render_html_receipt(ctx), media_type="text/html")


@router.get("/registrations/{registration_id}/receipt/pdf")
async def get_registration_receipt_pdf(
    registration_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    receipt_token: str | None = Query(default=None, alias="receipt_token"),
):
    registration = await get_registration_or_404(db, registration_id)
    await _assert_paid_receipt(registration)

    await authorize_payment_receipt_access(
        registration.payment_id,
        db=db,
        credentials=credentials,
        receipt_token=receipt_token,
    )

    from app.services.receipt_service import get_receipt_data_context, render_pdf_receipt

    await ensure_receipt(db, registration.payment_id)
    await db.commit()

    ctx = await get_receipt_data_context(db, registration.payment_id)
    pdf_bytes = render_pdf_receipt(ctx)
    filename = f"KRATOS-26-{ctx['receipt_number']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
