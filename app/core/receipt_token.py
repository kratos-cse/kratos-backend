"""Short-lived, receipt-scoped access tokens (not session JWTs)."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Tuple

import jwt
from fastapi import HTTPException, status

from app.core.config import settings

RECEIPT_TOKEN_PURPOSE = "receipt"
RECEIPT_TOKEN_AUDIENCE = "receipt"


def create_receipt_access_token(payment_id: uuid.UUID, profile_id: uuid.UUID) -> Tuple[str, int]:
    """Issue a JWT valid only for viewing one payment's receipt."""
    expire_minutes = settings.RECEIPT_ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(profile_id),
        "payment_id": str(payment_id),
        "purpose": RECEIPT_TOKEN_PURPOSE,
        "aud": RECEIPT_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire_minutes * 60


def decode_receipt_access_token(token: str, expected_payment_id: uuid.UUID) -> uuid.UUID:
    """
    Validate receipt token and return the profile_id (subject).
    Fails closed — cannot be used as a session JWT.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=RECEIPT_TOKEN_AUDIENCE,
            options={"require": ["exp", "sub", "payment_id", "purpose", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Receipt token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid receipt token")

    if payload.get("purpose") != RECEIPT_TOKEN_PURPOSE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid receipt token")

    try:
        profile_id = uuid.UUID(payload["sub"])
        payment_id = uuid.UUID(payload["payment_id"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid receipt token")

    if payment_id != expected_payment_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Receipt token not valid for this payment")

    return profile_id
