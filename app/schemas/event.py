import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    CapacityType,
    EventCategory,
    EventRegistrationStatus,
    EventSlot,
    EventVisibility,
    GenderCategory,
    MemberRegistrationMode,
    RegistrationAvailability,
    RegistrationMode,
)
from app.schemas.event_content import ContentSectionOut, CoordinatorOut


class EventListItem(BaseModel):
    """GET /events — lightweight catalogue card (no capacity calc, no long_desc/WhatsApp)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tagline: str | None = None
    short_desc: str | None
    category: EventCategory | None
    subcategory: str | None = None
    gender_category: GenderCategory = GenderCategory.OPEN
    fee: Decimal | None
    venue: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    slot: EventSlot | None
    visibility: EventVisibility
    registration_status: EventRegistrationStatus
    registration_open: bool
    registration_availability: RegistrationAvailability
    spots_remaining: int | None = None
    allow_individual: bool
    registration_mode: RegistrationMode | None = None
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
    subcategory: str | None = None
    gender_category: GenderCategory = GenderCategory.OPEN
    coordinator: str | None = None
    coord_contact: str | None = None
    content_sections: list[ContentSectionOut] = []
    coordinators: list[CoordinatorOut] = []
    fee: Decimal | None
    venue: str | None
    capacity: int | None
    whatsapp_group_available: bool = False
    starts_at: datetime | None
    ends_at: datetime | None
    slot: EventSlot | None
    visibility: EventVisibility
    registration_status: EventRegistrationStatus
    registration_open: bool
    registration_availability: RegistrationAvailability
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
    custom_fields: dict[str, Any] | None = None


class EventWhatsAppOut(BaseModel):
    event_id: uuid.UUID
    whatsapp_group_link: str
