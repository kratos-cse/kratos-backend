"""Authoritative event visibility + registration transitions."""
from fastapi import HTTPException, status

from app.models.enums import EventRegistrationStatus, EventVisibility
from app.models.event import Event


def enforce_event_invariants(event: Event) -> None:
    """UNPUBLISHED events must never have registration OPEN or COMING_SOON."""
    if event.visibility == EventVisibility.UNPUBLISHED and event.registration_status in (
        EventRegistrationStatus.OPEN,
        EventRegistrationStatus.COMING_SOON,
    ):
        event.registration_status = EventRegistrationStatus.CLOSED


def publish_event(event: Event) -> None:
    event.visibility = EventVisibility.PUBLISHED
    if event.registration_status == EventRegistrationStatus.CLOSED:
        event.registration_status = EventRegistrationStatus.COMING_SOON
    enforce_event_invariants(event)


def unpublish_event(event: Event) -> None:
    event.visibility = EventVisibility.UNPUBLISHED
    event.registration_status = EventRegistrationStatus.CLOSED


def mark_event_coming_soon(event: Event) -> None:
    if event.visibility != EventVisibility.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mark an unpublished event as coming soon.",
        )
    if event.registration_status == EventRegistrationStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Close registration before marking as coming soon.",
        )
    event.registration_status = EventRegistrationStatus.COMING_SOON


def open_registration(event: Event) -> None:
    if event.visibility != EventVisibility.PUBLISHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot open registration for an unpublished event.",
        )
    event.registration_status = EventRegistrationStatus.OPEN


def close_registration(event: Event) -> None:
    event.registration_status = EventRegistrationStatus.CLOSED
