"""Unit tests for event catalogue hardening (no live DB required)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.enums import EventCategory, EventStatus, RegistrationAvailability
from app.services.event_service import (
    get_cached_events_list,
    invalidate_events_list_cache,
    is_registration_open,
    resolve_registration_availability,
    set_cached_events_list,
)


def test_event_category_values_locked():
    assert {c.value for c in EventCategory} == {
        "TECHNICAL",
        "PLAYGROUND",
        "SPARK",
        "ONLINE",
        "CULTURAL",
    }
    assert "SPORTS" not in {c.value for c in EventCategory}


def test_registration_open_respects_status_and_window():
    now = datetime.now(timezone.utc)
    event = SimpleNamespace(status=EventStatus.OPEN)
    rules = SimpleNamespace(
        registration_opens_at=now - timedelta(hours=1),
        registration_closes_at=now + timedelta(hours=1),
    )
    assert is_registration_open(event, rules) is True

    event_closed = SimpleNamespace(status=EventStatus.CLOSED)
    assert is_registration_open(event_closed, rules) is False

    rules_future = SimpleNamespace(
        registration_opens_at=now + timedelta(hours=1),
        registration_closes_at=now + timedelta(hours=2),
    )
    assert is_registration_open(event, rules_future) is False


def test_resolve_registration_availability_precedence():
    now = datetime.now(timezone.utc)
    event_open = SimpleNamespace(status=EventStatus.OPEN)
    rules = SimpleNamespace(
        registration_opens_at=now - timedelta(hours=1),
        registration_closes_at=now + timedelta(hours=1),
    )

    assert (
        resolve_registration_availability(event_open, rules, 5) == RegistrationAvailability.OPEN
    )
    assert (
        resolve_registration_availability(event_open, rules, 0) == RegistrationAvailability.FULL
    )
    assert (
        resolve_registration_availability(
            SimpleNamespace(status=EventStatus.CLOSED), rules, 5
        )
        == RegistrationAvailability.EVENT_CLOSED
    )
    assert (
        resolve_registration_availability(
            event_open,
            SimpleNamespace(
                registration_opens_at=now + timedelta(hours=1),
                registration_closes_at=now + timedelta(hours=2),
            ),
            5,
        )
        == RegistrationAvailability.NOT_YET_OPEN
    )
    assert (
        resolve_registration_availability(
            event_open,
            SimpleNamespace(
                registration_opens_at=now - timedelta(hours=2),
                registration_closes_at=now - timedelta(hours=1),
            ),
            5,
        )
        == RegistrationAvailability.WINDOW_CLOSED
    )


def test_event_list_schema_includes_registration_availability():
    from app.schemas.event import EventListItem

    assert "registration_availability" in EventListItem.model_fields
    assert "spots_remaining" in EventListItem.model_fields
    assert "registration_mode" in EventListItem.model_fields


def test_events_list_cache_roundtrip():
    invalidate_events_list_cache()
    assert get_cached_events_list() is None
    set_cached_events_list([{"id": "x"}])
    assert get_cached_events_list() == [{"id": "x"}]
    invalidate_events_list_cache()
    assert get_cached_events_list() is None


def test_event_list_schema_has_tagline_not_whatsapp():
    from app.schemas.event import EventDetail, EventListItem

    assert "tagline" in EventListItem.model_fields
    assert "whatsapp_group_link" not in EventListItem.model_fields
    assert "whatsapp_group_link" not in EventDetail.model_fields
    assert "whatsapp_group_available" in EventDetail.model_fields


def test_whatsapp_route_registered():
    from app.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/events/{event_id}/whatsapp" in paths


def test_http_exception_maps_already_registered():
    from app.core.errors import ALREADY_REGISTERED, http_code_from_detail

    assert http_code_from_detail(409, "You are already registered for this event") == ALREADY_REGISTERED
