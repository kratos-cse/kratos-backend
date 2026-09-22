import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    CapacityType,
    EventCategory,
    EventSlot,
    EventStatus,
    MemberRegistrationMode,
    RegistrationMode,
)


class EventListItem(BaseModel):
    """GET /events — lightweight catalogue card (no capacity calc, no long_desc/WhatsApp)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tagline: str | None = None
    short_desc: str | None
    category: EventCategory | None
    fee: Decimal | None
    venue: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    slot: EventSlot | None
    status: EventStatus
    registration_open: bool
    allow_individual: bool
    team_min_size: int
    team_max_size: int
    required_member_count: int = 1
    substitute_count: int = 0


class EventDetail(BaseModel):
    """GET /events/{event_id} — registration-ready config. WhatsApp URL is not public."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tagline: str | None = None
    short_desc: str | None
    long_desc: str | None
    category: EventCategory | None
    coordinator: str | None
    coord_contact: str | None
    fee: Decimal | None
    venue: str | None
    capacity: int | None
    whatsapp_group_available: bool = False
    starts_at: datetime | None
    ends_at: datetime | None
    slot: EventSlot | None
    status: EventStatus
    registration_open: bool
    spots_remaining: int | None

    team_min_size: int
    team_max_size: int
    required_member_count: int = 1
    substitute_count: int = 0
    allow_individual: bool
    registration_mode: RegistrationMode | None
    capacity_type: CapacityType | None
    member_registration_mode: MemberRegistrationMode | None
    allow_team_invite_flow: bool
    requires_qr_checkin: bool
    custom_fields: dict[str, Any] | None
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None


class EventWhatsAppOut(BaseModel):
    event_id: uuid.UUID
    whatsapp_group_link: str
