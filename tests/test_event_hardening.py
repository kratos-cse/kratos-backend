"""Unit tests for event visibility + registration model (no live DB required)."""
from types import SimpleNamespace

from app.models.enums import (
    EventCategory,
    EventRegistrationStatus,
    EventVisibility,
    RegistrationAvailability,
)
from app.services.event_service import (
    get_cached_events_list,
    invalidate_events_list_cache,
    is_registration_open,
    resolve_registration_availability,
    set_cached_events_list,
)
from app.services.event_state import (
    close_registration,
    open_registration,
    publish_event,
    unpublish_event,
)


def _event(**kwargs):
    defaults = {
        "visibility": EventVisibility.PUBLISHED,
        "registration_status": EventRegistrationStatus.OPEN,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_event_category_values_locked():
    assert {c.value for c in EventCategory} == {
        "TECHNICAL",
        "PLAYGROUND",
        "SPARK",
        "ONLINE",
        "CULTURAL",
    }
    assert "SPORTS" not in {c.value for c in EventCategory}


def test_registration_open_requires_published_and_open():
    event = _event()
    assert is_registration_open(event, None) is True

    assert is_registration_open(_event(visibility=EventVisibility.UNPUBLISHED), None) is False
    assert (
        is_registration_open(
            _event(registration_status=EventRegistrationStatus.CLOSED),
            None,
        )
        is False
    )


def test_resolve_registration_availability_precedence():
    event = _event()
    assert resolve_registration_availability(event, None, 5) == RegistrationAvailability.OPEN
    assert resolve_registration_availability(event, None, 0) == RegistrationAvailability.FULL
    assert (
        resolve_registration_availability(
            _event(registration_status=EventRegistrationStatus.CLOSED),
            None,
            5,
        )
        == RegistrationAvailability.CLOSED
    )
    assert (
        resolve_registration_availability(
            _event(visibility=EventVisibility.UNPUBLISHED),
            None,
            5,
        )
        == RegistrationAvailability.CLOSED
    )


def test_open_registration_requires_published():
    import pytest
    from fastapi import HTTPException

    event = _event(
        visibility=EventVisibility.UNPUBLISHED,
        registration_status=EventRegistrationStatus.CLOSED,
    )
    with pytest.raises(HTTPException) as exc:
        open_registration(event)
    assert exc.value.status_code == 400


def test_event_state_transitions():
    event = _event(
        visibility=EventVisibility.UNPUBLISHED,
        registration_status=EventRegistrationStatus.CLOSED,
    )
    publish_event(event)
    assert event.visibility == EventVisibility.PUBLISHED
    assert event.registration_status == EventRegistrationStatus.CLOSED

    open_registration(event)
    assert event.registration_status == EventRegistrationStatus.OPEN

    close_registration(event)
    assert event.registration_status == EventRegistrationStatus.CLOSED

    open_registration(event)
    unpublish_event(event)
    assert event.visibility == EventVisibility.UNPUBLISHED
    assert event.registration_status == EventRegistrationStatus.CLOSED


def test_event_list_schema_includes_visibility_and_registration_status():
    from app.schemas.event import EventListItem

    assert "visibility" in EventListItem.model_fields
    assert "registration_status" in EventListItem.model_fields
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
