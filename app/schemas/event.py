import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import CapacityType, EventStatus, FeeChargeModel, MemberRegistrationMode


class EventListItem(BaseModel):
    """GET /events — catalogue + registration availability/status + basic pricing/team info."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    short_desc: str | None
    category: str | None
    fee: Decimal | None
    venue: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    status: EventStatus
    registration_open: bool
    allow_individual: bool
    team_min_size: int
    team_max_size: int


class EventDetail(BaseModel):
    """GET /events/{event_id} — complete config needed by the registration frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    short_desc: str | None
    long_desc: str | None
    category: str | None
    coordinator: str | None
    coord_contact: str | None
    fee: Decimal | None
    venue: str | None
    capacity: int | None
    whatsapp_group_link: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    status: EventStatus
    registration_open: bool
    spots_remaining: int | None

    # Registration rules
    team_min_size: int
    team_max_size: int
    allow_individual: bool
    fee_charge_model: FeeChargeModel | None
    capacity_type: CapacityType | None
    member_registration_mode: MemberRegistrationMode | None
    allow_team_invite_flow: bool
    requires_qr_checkin: bool
    custom_fields: dict[str, Any] | None
    registration_opens_at: datetime | None
    registration_closes_at: datetime | None
