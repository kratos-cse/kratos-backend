import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

RosterStyle = Literal["FIXED", "RANGE", "MEMBERS_SUBSTITUTES"]

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.roster_service import MAX_REQUIRED_MEMBERS, MAX_SUBSTITUTE_SLOTS

from app.models.enums import (
    CapacityType,
    EventCategory,
    EventSlot,
    EventVisibility,
    MemberRegistrationMode,
    PaymentStatus,
    PaymentType,
    RegistrationMode,
    RegistrationStatus,
    TeamStatus,
)


class AdminEventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    tagline: Optional[str] = Field(default=None, max_length=300)
    short_desc: Optional[str] = None
    long_desc: Optional[str] = None
    category: Optional[EventCategory] = None
    coordinator: Optional[str] = None
    coord_contact: Optional[str] = None
    fee: Optional[Decimal] = None
    venue: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=0)
    whatsapp_group_link: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    slot: Optional[EventSlot] = None

    registration_mode: RegistrationMode = RegistrationMode.TEAM_OR_INDIVIDUAL
    team_min_size: int = Field(default=1, ge=1, le=MAX_REQUIRED_MEMBERS)
    team_max_size: int = Field(default=1, ge=1, le=MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS)
    required_member_count: Optional[int] = Field(default=None, ge=1, le=MAX_REQUIRED_MEMBERS)
    substitute_count: Optional[int] = Field(default=None, ge=0, le=MAX_SUBSTITUTE_SLOTS)
    allow_team_invite_flow: bool = False
    requires_qr_checkin: bool = True
    capacity_type: CapacityType = CapacityType.PARTICIPANTS
    member_registration_mode: MemberRegistrationMode = MemberRegistrationMode.SELF_ENTRY
    roster_style: Optional[RosterStyle] = None
    custom_fields: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def sync_roster_team_sizes(self) -> "AdminEventCreate":
        if self.team_max_size < self.team_min_size:
            raise ValueError("team_max_size must be greater than or equal to team_min_size.")
        if self.required_member_count is not None or self.substitute_count is not None:
            req = max(1, min(MAX_REQUIRED_MEMBERS, int(self.required_member_count or self.team_min_size or 1)))
            if self.substitute_count is not None:
                subs = max(0, min(MAX_SUBSTITUTE_SLOTS, int(self.substitute_count)))
            else:
                subs = max(0, min(MAX_SUBSTITUTE_SLOTS, int(self.team_max_size) - int(self.team_min_size)))
            self.required_member_count = req
            self.substitute_count = subs
            self.team_min_size = req
            self.team_max_size = req + subs
        else:
            mn = max(1, min(MAX_REQUIRED_MEMBERS, int(self.team_min_size)))
            mx = max(mn, min(MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS, int(self.team_max_size)))
            self.team_min_size = mn
            self.team_max_size = mx
            self.required_member_count = mn
            self.substitute_count = mx - mn
        return self


class AdminEventUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    tagline: Optional[str] = Field(default=None, max_length=300)
    short_desc: Optional[str] = None
    long_desc: Optional[str] = None
    category: Optional[EventCategory] = None
    coordinator: Optional[str] = None
    coord_contact: Optional[str] = None
    fee: Optional[Decimal] = None
    venue: Optional[str] = None
    capacity: Optional[int] = Field(default=None, ge=0)
    whatsapp_group_link: Optional[str] = None
    google_sheet_id: Optional[str] = None
    google_sheet_url: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    slot: Optional[EventSlot] = None


class AdminRegistrationRulesUpdate(BaseModel):
    registration_mode: Optional[RegistrationMode] = None
    roster_style: Optional[RosterStyle] = None
    team_min_size: Optional[int] = Field(default=None, ge=1, le=MAX_REQUIRED_MEMBERS)
    team_max_size: Optional[int] = Field(
        default=None, ge=1, le=MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS
    )
    required_member_count: Optional[int] = Field(default=None, ge=1, le=MAX_REQUIRED_MEMBERS)
    substitute_count: Optional[int] = Field(default=None, ge=0, le=MAX_SUBSTITUTE_SLOTS)
    allow_team_invite_flow: Optional[bool] = None
    requires_qr_checkin: Optional[bool] = None
    capacity_type: Optional[CapacityType] = None
    member_registration_mode: Optional[MemberRegistrationMode] = None
    custom_fields: Optional[dict[str, Any]] = None


class AdminParticipantUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=20)
    college_name: Optional[str] = Field(default=None, max_length=200)
    department: Optional[str] = Field(default=None, max_length=200)
    year_of_study: Optional[str] = Field(default=None, max_length=50)


class AdminManualRegister(BaseModel):
    profile_id: uuid.UUID
    event_id: uuid.UUID
    mark_paid: bool = False


class AdminTeamUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[TeamStatus] = None


class TransferLeadershipBody(BaseModel):
    new_leader_profile_id: uuid.UUID


class AdminRegistrationUpdate(BaseModel):
    status: Optional[RegistrationStatus] = None


class AdminRegistrationPaymentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payer_profile_id: uuid.UUID
    payment_type: PaymentType
    amount_paise: int
    currency: str
    status: PaymentStatus
    razorpay_order_id: str
    razorpay_payment_id: Optional[str] = None
    created_at: datetime


class AdminRegistrationTeamSummary(BaseModel):
    id: uuid.UUID
    name: str
    status: TeamStatus
    leader_profile_id: uuid.UUID
    active_member_count: int
    required_member_count: int
    substitute_count: int
    team_max_size: int
    mandatory_filled: int
    substitutes_filled: int


class AdminRegistrationListItem(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    event_name: Optional[str] = None
    profile_id: Optional[uuid.UUID] = None
    team_id: Optional[uuid.UUID] = None
    registration_type: str
    status: RegistrationStatus
    payment_id: Optional[uuid.UUID] = None
    payment_status: Optional[PaymentStatus] = None
    payment: Optional[AdminRegistrationPaymentSummary] = None
    team: Optional[AdminRegistrationTeamSummary] = None
    created_at: datetime


class PaymentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    payer_profile_id: uuid.UUID
    payment_type: PaymentType
    amount_paise: int
    currency: str
    status: PaymentStatus
    razorpay_order_id: str
    razorpay_payment_id: Optional[str] = None
    created_at: datetime
