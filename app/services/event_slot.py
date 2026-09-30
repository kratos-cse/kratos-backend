"""Derive event slot from schedule when admin does not set one explicitly."""
from datetime import datetime

from app.models.enums import EventSlot


def derive_event_slot(
    starts_at: datetime | None,
    ends_at: datetime | None = None,
) -> EventSlot:
    if starts_at is None:
        return EventSlot.FULL_DAY

    start_day = starts_at.date()
    if ends_at is not None and ends_at.date() > start_day:
        return EventSlot.MULTI_DAY

    hour = starts_at.hour
    if hour < 12:
        return EventSlot.MORNING
    if hour < 17:
        return EventSlot.AFTERNOON
    return EventSlot.EVENING
