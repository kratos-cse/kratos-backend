"""Server-side amount calculation — the ONLY source of truth for charges."""
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event


async def fetch_fee_rupees(db: AsyncSession, event_id: UUID) -> float:
    result = await db.execute(select(Event.fee).where(Event.id == event_id))
    fee = result.scalar_one_or_none()
    if fee is None:
        raise ValueError(f"Could not load fee for event {event_id}")
    return float(fee)


def compute_amount_paise_from_fee(fee_rupees: float) -> int:
    if fee_rupees < 0:
        raise ValueError(f"Invalid fee: {fee_rupees}")
    return int(round(Decimal(str(fee_rupees)) * 100))


async def compute_amount_paise(db: AsyncSession, event_id: UUID) -> int:
    fee_rupees = await fetch_fee_rupees(db, event_id)
    return compute_amount_paise_from_fee(fee_rupees)
