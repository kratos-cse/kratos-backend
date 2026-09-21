"""
Google Sign-In verification.

POST /auth/google expects the FRONTEND to have already run Google Sign-In
and to send us the resulting id_token. We verify that token's signature
and audience against GOOGLE_CLIENT_ID here — we never handle the user's
Google password or do the OAuth redirect dance ourselves.

Until GOOGLE_CLIENT_ID is set (see .env.example), this raises a clear 500
instead of silently accepting unverified tokens.
"""
from dataclasses import dataclass

from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings

_VALID_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


@dataclass
class GoogleTokenPayload:
    sub: str
    email: str
    name: str | None
    email_verified: bool


def verify_google_id_token(token: str) -> GoogleTokenPayload:
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_CLIENT_ID is not configured on this server yet.",
        )

    try:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid Google token: {exc}")

    if payload.get("iss") not in _VALID_ISSUERS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token issuer")

    return GoogleTokenPayload(
        sub=payload["sub"],
        email=payload["email"],
        name=payload.get("name"),
        email_verified=payload.get("email_verified", False),
    )
