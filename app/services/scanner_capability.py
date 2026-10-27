"""Short-lived tokens that authorize check-in for one team at one checkpoint."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, status

from app.core.config import settings

SCANNER_TOKEN_PURPOSE = "scanner_team"
SCANNER_TOKEN_AUDIENCE = "scanner"
SCANNER_CAPABILITY_MINUTES = 20


@dataclass(frozen=True)
class ScannerTeamScope:
    team_id: uuid.UUID
    event_id: uuid.UUID
    checkpoint_id: uuid.UUID


def issue_team_capability(
    *,
    team_id: uuid.UUID,
    event_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "purpose": SCANNER_TOKEN_PURPOSE,
        "aud": SCANNER_TOKEN_AUDIENCE,
        "team_id": str(team_id),
        "event_id": str(event_id),
        "checkpoint_id": str(checkpoint_id),
        "iat": now,
        "exp": now + timedelta(minutes=SCANNER_CAPABILITY_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_team_capability(token: str) -> ScannerTeamScope:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=SCANNER_TOKEN_AUDIENCE,
            options={"require": ["exp", "purpose", "aud", "team_id", "event_id", "checkpoint_id"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Check-in session expired. Scan the QR again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Check-in session is not valid. Scan the QR again.")

    if payload.get("purpose") != SCANNER_TOKEN_PURPOSE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Check-in session is not valid. Scan the QR again.")

    try:
        return ScannerTeamScope(
            team_id=uuid.UUID(payload["team_id"]),
            event_id=uuid.UUID(payload["event_id"]),
            checkpoint_id=uuid.UUID(payload["checkpoint_id"]),
        )
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Check-in session is not valid. Scan the QR again.")
