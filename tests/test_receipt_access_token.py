"""Receipt-scoped access tokens — fast unit tests + minimal DB integration."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.api.deps_receipt import authorize_payment_receipt_access
from app.core.config import settings
from app.core.receipt_token import create_receipt_access_token, decode_receipt_access_token
from app.core.security import create_access_token, decode_token
from app.models.enums import PaymentStatus, RegistrationStatus
from app.services import qr_service
from app.services.receipt_service import ensure_receipt, get_receipt_data_context, render_html_receipt
from tests.conftest import _make_user_profile, requires_db


async def _prepare_paid_receipt(db, solo_payment_setup):
    setup = solo_payment_setup
    payment = setup["payment"]
    registration = setup["registration"]
    payment.status = PaymentStatus.PAID
    registration.status = RegistrationStatus.CONFIRMED
    await qr_service.generate_for_registration(db, registration.id)
    await ensure_receipt(db, payment.id)
    await db.flush()
    return setup


# --- Fast unit tests (no database) -------------------------------------------------


def test_create_and_decode_receipt_token_roundtrip():
    payment_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    token, expires_in = create_receipt_access_token(payment_id, profile_id)
    assert expires_in > 0
    assert decode_receipt_access_token(token, payment_id) == profile_id


def test_expired_receipt_token_rejected_unit():
    payment_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    expired = jwt.encode(
        {
            "sub": str(profile_id),
            "payment_id": str(payment_id),
            "purpose": "receipt",
            "aud": "receipt",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        decode_receipt_access_token(expired, payment_id)
    assert exc.value.status_code == 401


def test_receipt_token_wrong_payment_rejected_unit():
    payment_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    token, _ = create_receipt_access_token(uuid.uuid4(), profile_id)
    with pytest.raises(HTTPException) as exc:
        decode_receipt_access_token(token, payment_id)
    assert exc.value.status_code == 403


def test_malformed_receipt_token_rejected_unit():
    with pytest.raises(HTTPException) as exc:
        decode_receipt_access_token("not-a-jwt", uuid.uuid4())
    assert exc.value.status_code == 401


def test_receipt_token_cannot_decode_as_session_jwt():
    payment_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    token, _ = create_receipt_access_token(payment_id, profile_id)
    with pytest.raises(HTTPException):
        decode_token(token)


def test_session_jwt_cannot_decode_as_receipt_token():
    user_id = uuid.uuid4()
    session_token, _ = create_access_token(user_id)
    with pytest.raises(HTTPException):
        decode_receipt_access_token(session_token, uuid.uuid4())


# --- DB integration (service layer, no TestClient) ----------------------------------


@pytest.mark.asyncio
@requires_db
async def test_valid_receipt_token_authorizes_and_renders_html(db, solo_payment_setup):
    setup = await _prepare_paid_receipt(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]
    token, _ = create_receipt_access_token(payment.id, profile.id)

    await authorize_payment_receipt_access(
        payment.id,
        db=db,
        credentials=None,
        receipt_token=token,
    )
    ctx = await get_receipt_data_context(db, payment.id)
    html = render_html_receipt(ctx)
    assert setup["event"].name in html


@pytest.mark.asyncio
@requires_db
async def test_receipt_token_wrong_user_rejected(db, solo_payment_setup):
    setup = await _prepare_paid_receipt(db, solo_payment_setup)
    payment = setup["payment"]
    other = await _make_user_profile(db, email=f"other-{uuid.uuid4().hex}@t.local")
    token, _ = create_receipt_access_token(payment.id, other.id)

    with pytest.raises(HTTPException) as exc:
        await authorize_payment_receipt_access(
            payment.id,
            db=db,
            credentials=None,
            receipt_token=token,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@requires_db
async def test_missing_auth_returns_401(db, solo_payment_setup):
    setup = await _prepare_paid_receipt(db, solo_payment_setup)
    payment = setup["payment"]

    with pytest.raises(HTTPException) as exc:
        await authorize_payment_receipt_access(
            payment.id,
            db=db,
            credentials=None,
            receipt_token=None,
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
@requires_db
async def test_bearer_jwt_still_works_for_receipt_access(db, solo_payment_setup):
    from fastapi.security import HTTPAuthorizationCredentials

    setup = await _prepare_paid_receipt(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]
    session_token, _ = create_access_token(profile.user_id)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=session_token)

    payer = await authorize_payment_receipt_access(
        payment.id,
        db=db,
        credentials=credentials,
        receipt_token=None,
    )
    assert payer.id == profile.id


@pytest.mark.asyncio
@requires_db
async def test_access_token_urls_are_scoped(db, solo_payment_setup):
    from app.services.receipt_service import receipt_html_url, receipt_pdf_url

    setup = await _prepare_paid_receipt(db, solo_payment_setup)
    payment = setup["payment"]
    profile = setup["profile"]
    session_token, _ = create_access_token(profile.user_id)

    token, _ = create_receipt_access_token(payment.id, profile.id)
    html_url = receipt_html_url(payment.id, token)
    assert "receipt_token=" in html_url
    assert session_token not in html_url
    pdf_url = receipt_pdf_url(payment.id, token)
    assert "receipt_token=" in pdf_url
