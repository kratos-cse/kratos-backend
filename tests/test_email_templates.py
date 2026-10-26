"""Email template catalog — rendering without database."""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.enums import EventCategory, EventRegistrationStatus, EventVisibility
from app.models.event import Event, EventRegistrationRule
from app.services.email_templates.catalog import (
    TemplateInputs,
    render_template,
    validate_template_inputs,
)


def _sample_event(**kwargs) -> Event:
    ev = Event(
        id=uuid.uuid4(),
        name="Sample Hackathon",
        category=EventCategory.TECHNICAL,
        venue="Main Auditorium",
        starts_at=datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc),
        ends_at=datetime(2026, 3, 15, 18, 0, tzinfo=timezone.utc),
        whatsapp_group_link="https://chat.whatsapp.com/AbCdEfGh",
        short_desc="Bring your laptop and college ID.",
        coordinator="ACE Team",
        coord_contact="kratos.cse@gmail.com",
        visibility=EventVisibility.PUBLISHED,
        registration_status=EventRegistrationStatus.OPEN,
    )
    for k, v in kwargs.items():
        setattr(ev, k, v)
    return ev


def test_announcement_renders_whatsapp_link():
    event = _sample_event()
    rendered = render_template(
        "event_announcement",
        event,
        TemplateInputs(subject="Hello", body="Important update for everyone."),
    )
    assert "Sample Hackathon" in rendered.html_body
    assert "chat.whatsapp.com" in rendered.html_body
    assert "Join the Event WhatsApp Group" in rendered.html_body
    assert "chat.whatsapp.com" in rendered.text_body


def test_whatsapp_omitted_when_invalid():
    event = _sample_event(whatsapp_group_link="https://evil.example/join")
    rendered = render_template(
        "event_announcement",
        event,
        TemplateInputs(subject="Hi", body="Note"),
    )
    assert "whatsapp" not in rendered.html_body.lower() or "join the event whatsapp" not in rendered.html_body.lower()


def test_deadline_template_requires_close_date():
    event = _sample_event()
    event.rules = None
    with pytest.raises(ValueError, match="registration close"):
        render_template("submission_deadline_reminder", event, TemplateInputs(subject="Due", body=""))


def test_schedule_update_requires_change_summary():
    event = _sample_event()
    with pytest.raises(ValueError, match="change"):
        validate_template_inputs(
            "schedule_venue_update",
            {"subject": "Moved", "body": ""},
        )
