"""QR token generation and lifecycle (registration or team member ownership)."""
import secrets
import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.models.qr_code import QRCode


async def get_by_id(db: AsyncSession, qr_id: uuid.UUID) -> Optional[QRCode]:
    result = await db.execute(select(QRCode).where(QRCode.id == qr_id))
    return result.scalar_one_or_none()


async def get_by_token(db: AsyncSession, token: str) -> Optional[QRCode]:
    result = await db.execute(select(QRCode).where(QRCode.token == token))
    return result.scalar_one_or_none()


async def deactivate_for_registration(db: AsyncSession, registration_id: uuid.UUID) -> None:
    await db.execute(
        update(QRCode)
        .where(QRCode.registration_id == registration_id, QRCode.is_active.is_(True))
        .values(is_active=False)
    )


async def deactivate_for_team_member(db: AsyncSession, team_member_id: uuid.UUID) -> None:
    await db.execute(
        update(QRCode)
        .where(QRCode.team_member_id == team_member_id, QRCode.is_active.is_(True))
        .values(is_active=False)
    )


async def generate_for_registration(db: AsyncSession, registration_id: uuid.UUID) -> QRCode:
    await deactivate_for_registration(db, registration_id)
    qr = QRCode(
        registration_id=registration_id,
        team_member_id=None,
        token=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(qr)
    await db.flush()
    return qr


async def generate_for_team_member(db: AsyncSession, team_member_id: uuid.UUID) -> QRCode:
    await deactivate_for_team_member(db, team_member_id)
    qr = QRCode(
        registration_id=None,
        team_member_id=team_member_id,
        token=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(qr)
    await db.flush()
    return qr


async def get_by_id_or_404(db: AsyncSession, qr_id: uuid.UUID) -> QRCode:
    qr = await get_by_id(db, qr_id)
    if qr is None:
        raise AppError(NOT_FOUND, "QR code not found", status_code=404)
    return qr
