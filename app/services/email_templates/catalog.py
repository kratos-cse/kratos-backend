"""Predefined KRATOS email templates (shared shell + render_email_html)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from app.models.event import Event
from app.services import notification_service
from app.services.email_content import (
    coordinator_contact_line,
    event_category_label,
    format_event_schedule,
    sanitize_whatsapp_group_url,
    whatsapp_cta_html,
)

TEMPLATE_IDS = (
    "event_announcement",
    "event_reminder",
    "schedule_venue_update",
    "submission_deadline_reminder",
    "registration_confirmation",
    "general_participant_message",
)


@dataclass
class TemplateVariable:
    key: str
    label: str
    required: bool = False
    field_type: str = "text"  # text | textarea


@dataclass
class EmailTemplate:
    id: str
    name: str
    description: str
    variables: list[TemplateVariable]
    supports_bulk: bool = True
    preview_only: bool = False


@dataclass
class RenderedTemplateEmail:
    subject: str
    text_body: str
    html_body: str


@dataclass
class TemplateInputs:
    subject: str = ""
    body: str = ""
    change_summary: str = ""
    deadline_note: str = ""


def _app_url() -> str:
    return notification_service._app_url()


def _event_detail_rows(event: Event) -> list[tuple[str, str]]:
    date_str, start_str, end_str = format_event_schedule(event)
    rows: list[tuple[str, str]] = [("Event", event.name)]
    cat = event_category_label(event)
    if cat:
        rows.append(("Category", cat))
    rows.append(("Date", date_str))
    time_val = start_str
    if end_str:
        time_val = f"{start_str} – {end_str}"
    rows.append(("Start time", time_val))
    if event.venue:
        rows.append(("Venue", event.venue))
    coord = coordinator_contact_line(event)
    if coord:
        rows.append(("Coordinator", coord))
    if event.short_desc:
        rows.append(("Instructions", event.short_desc.strip()))
    return rows


def _paragraphs_from_body(body: str) -> list[str]:
    return [p.strip() for p in body.replace("\r\n", "\n").split("\n") if p.strip()]


def _render_bulk(
    *,
    event: Event,
    inputs: TemplateInputs,
    greeting: str,
    default_subject: str,
    extra_paragraphs: Optional[list[str]] = None,
    include_event_details: bool = True,
) -> RenderedTemplateEmail:
    subject = (inputs.subject or default_subject).strip()
    paras = _paragraphs_from_body(inputs.body)
    if extra_paragraphs:
        paras = extra_paragraphs + paras
    details = _event_detail_rows(event) if include_event_details else None
    wa = sanitize_whatsapp_group_url(event.whatsapp_group_link)
    html_body = notification_service.render_email_html(
        title=subject,
        greeting=greeting,
        paragraphs=paras,
        details=details,
        cta_url=_app_url(),
        cta_label="Open KRATOS",
        extra_body_html=whatsapp_cta_html(wa) if wa else None,
    )

    text_lines = [greeting, ""]
    text_lines.extend(paras)
    if include_event_details:
        text_lines.append("")
        text_lines.append("EVENT DETAILS")
        for k, v in _event_detail_rows(event):
            text_lines.append(f"  {k}: {v}")
    if wa:
        text_lines.extend(["", f"Join the Event WhatsApp Group: {wa}"])
    text_lines.extend(["", f"Open KRATOS: {_app_url()}"])
    return RenderedTemplateEmail(subject=subject, text_body="\n".join(text_lines), html_body=html_body)


_TEMPLATES: dict[str, EmailTemplate] = {
    "event_announcement": EmailTemplate(
        id="event_announcement",
        name="Event Announcement",
        description="General update for confirmed participants of an event.",
        variables=[
            TemplateVariable("subject", "Subject", required=True),
            TemplateVariable("body", "Message", required=True, field_type="textarea"),
        ],
    ),
    "event_reminder": EmailTemplate(
        id="event_reminder",
        name="Event Reminder",
        description="Remind participants of date, time, venue and instructions.",
        variables=[
            TemplateVariable("subject", "Subject", required=True),
            TemplateVariable("body", "Additional notes", required=False, field_type="textarea"),
        ],
    ),
    "schedule_venue_update": EmailTemplate(
        id="schedule_venue_update",
        name="Schedule or Venue Update",
        description="Highlight schedule or venue changes.",
        variables=[
            TemplateVariable("subject", "Subject", required=True),
            TemplateVariable("change_summary", "What changed", required=True, field_type="textarea"),
            TemplateVariable("body", "Extra details", required=False, field_type="textarea"),
        ],
    ),
    "submission_deadline_reminder": EmailTemplate(
        id="submission_deadline_reminder",
        name="Submission Deadline Reminder",
        description="Reminder when the event has a registration close date configured.",
        variables=[
            TemplateVariable("subject", "Subject", required=True),
            TemplateVariable("body", "Message", required=False, field_type="textarea"),
        ],
    ),
    "registration_confirmation": EmailTemplate(
        id="registration_confirmation",
        name="Registration Confirmation",
        description="Transactional confirmation (sent automatically after payment). Preview only.",
        variables=[],
        supports_bulk=False,
        preview_only=True,
    ),
    "general_participant_message": EmailTemplate(
        id="general_participant_message",
        name="General Participant Message",
        description="Approved administrative message to confirmed participants.",
        variables=[
            TemplateVariable("subject", "Subject", required=True),
            TemplateVariable("body", "Message", required=True, field_type="textarea"),
        ],
    ),
}


_RENDERERS: dict[str, Callable[[Event, TemplateInputs], RenderedTemplateEmail]] = {}


def _register_renderer(template_id: str, fn: Callable[[Event, TemplateInputs], RenderedTemplateEmail]) -> None:
    _RENDERERS[template_id] = fn


def _render_announcement(event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    return _render_bulk(
        event=event,
        inputs=inputs,
        greeting="Announcement from KRATOS'26",
        default_subject=f"Update — {event.name}",
    )


def _render_reminder(event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    date_str, start_str, end_str = format_event_schedule(event)
    intro = [
        f"This is a reminder for {event.name} on {date_str} at {start_str}."
        + (f" The event ends around {end_str}." if end_str else "")
    ]
    if event.venue:
        intro.append(f"Venue: {event.venue}.")
    return _render_bulk(
        event=event,
        inputs=inputs,
        greeting="Reminder from KRATOS'26",
        default_subject=f"Reminder — {event.name}",
        extra_paragraphs=intro,
    )


def _render_schedule_update(event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    summary_paras = _paragraphs_from_body(inputs.change_summary)
    merged = TemplateInputs(
        subject=inputs.subject,
        body=inputs.body,
    )
    return _render_bulk(
        event=event,
        inputs=merged,
        greeting="Schedule / venue update",
        default_subject=f"Update — {event.name}",
        extra_paragraphs=["Please note the following change:"] + summary_paras,
    )


def _render_deadline(event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    closes: Optional[datetime] = None
    if event.rules and event.rules.registration_closes_at:
        closes = event.rules.registration_closes_at
    if not closes:
        raise ValueError("This event has no registration close date configured.")
    when = closes.strftime("%d %b %Y %I:%M %p").lstrip("0")
    intro = [f"Registration for {event.name} closes on {when}."]
    return _render_bulk(
        event=event,
        inputs=inputs,
        greeting="Deadline reminder",
        default_subject=f"Deadline approaching — {event.name}",
        extra_paragraphs=intro,
    )


def _render_general(event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    return _render_bulk(
        event=event,
        inputs=inputs,
        greeting="Message from KRATOS'26",
        default_subject=f"Message — {event.name}",
        include_event_details=False,
    )


def _render_confirmation_preview(event: Event, _inputs: TemplateInputs) -> RenderedTemplateEmail:
    from app.services.confirmation_email import render_confirmation_email
    import uuid

    dummy_reg = uuid.uuid4()
    content = render_confirmation_email(
        participant_name="Preview Participant",
        event=event,
        registration_type="SOLO",
        registration_id=dummy_reg,
        payment=None,
        receipt_number=None,
        receipt_html=None,
        receipt_pdf=None,
        registration_page_url=f"{_app_url()}/registrations/{dummy_reg}",
    )
    return RenderedTemplateEmail(
        subject=content.subject,
        text_body=content.text_body,
        html_body=content.html_body,
    )


_register_renderer("event_announcement", _render_announcement)
_register_renderer("event_reminder", _render_reminder)
_register_renderer("schedule_venue_update", _render_schedule_update)
_register_renderer("submission_deadline_reminder", _render_deadline)
_register_renderer("general_participant_message", _render_general)
_register_renderer("registration_confirmation", _render_confirmation_preview)


def list_templates() -> list[dict[str, Any]]:
    out = []
    for t in _TEMPLATES.values():
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "supports_bulk": t.supports_bulk,
                "preview_only": t.preview_only,
                "variables": [
                    {"key": v.key, "label": v.label, "required": v.required, "field_type": v.field_type}
                    for v in t.variables
                ],
            }
        )
    return out


def get_template(template_id: str) -> EmailTemplate:
    if template_id not in _TEMPLATES:
        raise KeyError(f"Unknown template: {template_id}")
    return _TEMPLATES[template_id]


def validate_template_inputs(template_id: str, raw: dict[str, Any]) -> TemplateInputs:
    tpl = get_template(template_id)
    inputs = TemplateInputs(
        subject=str(raw.get("subject") or "").strip(),
        body=str(raw.get("body") or "").strip(),
        change_summary=str(raw.get("change_summary") or "").strip(),
        deadline_note=str(raw.get("deadline_note") or "").strip(),
    )
    for var in tpl.variables:
        val = getattr(inputs, var.key, "") if hasattr(inputs, var.key) else str(raw.get(var.key) or "").strip()
        if var.required and not val:
            raise ValueError(f"Missing required field: {var.label}")
    return inputs


def render_template(template_id: str, event: Event, inputs: TemplateInputs) -> RenderedTemplateEmail:
    if template_id not in _RENDERERS:
        raise KeyError(f"Unknown template: {template_id}")
    return _RENDERERS[template_id](event, inputs)
