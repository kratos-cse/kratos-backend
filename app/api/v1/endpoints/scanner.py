"""Unauthenticated event check-in used by the standalone QR scanner."""
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import scanner_service
from app.services.scanner_rate_limit import enforce_scanner_rate_limit

router = APIRouter(prefix="/scanner", tags=["Scanner"])


class ScannerScanBody(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    checkpoint_id: uuid.UUID


class ScannerMemberCheckInBody(BaseModel):
    capability: str = Field(min_length=20, max_length=2048)
    team_member_id: uuid.UUID


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


@router.get("/events")
async def scanner_events(db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": await scanner_service.list_scanner_events(db)}


@router.get("/checkpoints")
async def scanner_checkpoints(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return {"status": "success", "data": await scanner_service.list_scanner_checkpoints(db, event_id)}


@router.post("/scan")
async def scanner_scan(
    body: ScannerScanBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    enforce_scanner_rate_limit(_client_key(request))
    data = await scanner_service.public_scan(db, body.token.strip(), body.checkpoint_id)
    return {"status": "success", "data": data}


@router.post("/member-checkin")
async def scanner_member_checkin(
    body: ScannerMemberCheckInBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    enforce_scanner_rate_limit(_client_key(request))
    data = await scanner_service.public_member_checkin(db, body.capability.strip(), body.team_member_id)
    return {"status": "success", "data": data}
