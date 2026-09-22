import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    TeamMemberEntrySource,
    TeamMemberRole,
    TeamMemberStatus,
    TeamStatus,
)


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    profile_id: uuid.UUID | None = None
    role: TeamMemberRole
    status: TeamMemberStatus
    joined_at: datetime
    entry_source: TeamMemberEntrySource = TeamMemberEntrySource.LINKED_ACCOUNT
    full_name: str | None = None
    phone: str | None = None
    contact_email: str | None = None
    college_name: str | None = None
    year_of_study: str | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    leader_profile_id: uuid.UUID
    status: TeamStatus
    created_at: datetime


class TeamDetailOut(TeamOut):
    active_member_count: int
    team_max_size: int
    required_member_count: int = 1
    substitute_count: int = 0
    mandatory_filled: int = 0
    substitutes_filled: int = 0
    members: list[TeamMemberOut] = []


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    code: str
    is_active: bool
    created_at: datetime


class InvitationPublicOut(BaseModel):
    team_id: uuid.UUID
    team_name: str
    event_id: uuid.UUID
    event_name: str
    leader_name: str
    active_member_count: int
    team_max_size: int
    required_member_count: int = 1
    substitute_count: int = 0
    is_full: bool
    is_active: bool


class JoinTeamResponse(BaseModel):
    team: TeamOut
    member: TeamMemberOut
    registration_id: uuid.UUID | None = None


class RosterAddRequest(BaseModel):
    """Leader adds a mandatory member or substitute — linked account and/or details."""

    role: TeamMemberRole = TeamMemberRole.MEMBER
    profile_id: Optional[uuid.UUID] = None
    full_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)
    contact_email: Optional[str] = Field(default=None, max_length=255)
    college_name: Optional[str] = Field(default=None, max_length=200)
    year_of_study: Optional[str] = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def require_identity(self) -> "RosterAddRequest":
        if self.role == TeamMemberRole.LEADER:
            raise ValueError("Cannot add a second leader via roster endpoint")
        if self.role not in (TeamMemberRole.MEMBER, TeamMemberRole.SUBSTITUTE):
            raise ValueError("role must be MEMBER or SUBSTITUTE")
        if not self.profile_id and not (self.full_name and self.phone):
            raise ValueError("Provide profile_id or full_name + phone for leader-entered members")
        return self
