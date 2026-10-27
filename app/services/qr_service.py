"""QR token generation and lifecycle (registration or team member ownership)."""
import logging
import secrets
import uuid
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.models.qr_code import QRCode

logger = logging.getLogger("qr_service")


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


async def _reactivate_or_create(
    db: AsyncSession,
    *,
    registration_id: Optional[uuid.UUID],
    team_member_id: Optional[uuid.UUID],
) -> QRCode:
    if registration_id is not None:
        lookup = select(QRCode).where(QRCode.registration_id == registration_id)
    else:
        lookup = select(QRCode).where(QRCode.team_member_id == team_member_id)

    result = await db.execute(lookup.with_for_update())
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.is_active = True
        await db.flush()
        return existing

    try:
        async with db.begin_nested():
            qr = QRCode(
                registration_id=registration_id,
                team_member_id=team_member_id,
                token=secrets.token_urlsafe(32),
                is_active=True,
            )
            db.add(qr)
            await db.flush()
            return qr
    except IntegrityError:
        result = await db.execute(lookup)
        existing = result.scalar_one_or_none()
        if existing is None:
            raise
        existing.is_active = True
        await db.flush()
        logger.info(
            "qr_generation_recovered_after_race registration_id=%s team_member_id=%s",
            registration_id,
            team_member_id,
        )
        return existing


async def generate_for_registration(db: AsyncSession, registration_id: uuid.UUID) -> QRCode:
    """Idempotent: at most one qr_codes row per registration_id (unique constraint)."""
    return await _reactivate_or_create(db, registration_id=registration_id, team_member_id=None)


async def generate_for_team_member(db: AsyncSession, team_member_id: uuid.UUID) -> QRCode:
    """Idempotent: at most one qr_codes row per team_member_id (unique constraint)."""
    return await _reactivate_or_create(db, registration_id=None, team_member_id=team_member_id)


async def get_by_id_or_404(db: AsyncSession, qr_id: uuid.UUID) -> QRCode:
    qr = await get_by_id(db, qr_id)
    if qr is None:
        raise AppError(NOT_FOUND, "QR code not found", status_code=404)
    return qr
