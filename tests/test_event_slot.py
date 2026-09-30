from datetime import datetime, timezone

from app.models.enums import EventSlot
from app.services.event_slot import derive_event_slot


def test_derive_slot_morning():
    starts = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
    assert derive_event_slot(starts, None) == EventSlot.MORNING


def test_derive_slot_multi_day():
    starts = datetime(2026, 3, 15, 9, 0, tzinfo=timezone.utc)
    ends = datetime(2026, 3, 16, 18, 0, tzinfo=timezone.utc)
    assert derive_event_slot(starts, ends) == EventSlot.MULTI_DAY


def test_derive_slot_full_day_without_start():
    assert derive_event_slot(None, None) == EventSlot.FULL_DAY
