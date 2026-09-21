import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import PaymentStatus, RegistrationStatus, TeamMemberRole, TeamMemberStatus, TeamStatus


class RegistrationType(str, Enum):
    SOLO = "SOLO"
    TEAM = "TEAM"


class RegistrationCreateRequest(BaseModel):
    registration_type: RegistrationType
    team_name: str | None = Field(default=None, min_length=1, max_length=150)

    @model_validator(mode="after")
    def team_name_required_for_team(self) -> "RegistrationCreateRequest":
        if self.registration_type == RegistrationType.TEAM and not self.team_name:
            raise ValueError("team_name is required when registration_type is TEAM")
        return self


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    role: TeamMemberRole
    status: TeamMemberStatus
    joined_at: datetime


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    leader_profile_id: uuid.UUID
    status: TeamStatus
    members: list[TeamMemberOut] = []


class PaymentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: PaymentStatus
    amount_paise: int
    currency: str


class RegistrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    status: RegistrationStatus
    created_at: datetime
    profile_id: uuid.UUID | None = None
    team: TeamOut | None = None
    payment: PaymentSummary | None = None


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    receipt_number: str
    pdf_url: str
    issued_at: datetime


class QRCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    token: str
    is_active: bool
    generated_at: datetime
