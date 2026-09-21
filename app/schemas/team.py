import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TeamMemberRole, TeamMemberStatus, TeamStatus


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TeamUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    profile_id: uuid.UUID
    role: TeamMemberRole
    status: TeamMemberStatus
    joined_at: datetime
    full_name: str | None = None


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
    is_full: bool
    is_active: bool


class JoinTeamResponse(BaseModel):
    team: TeamOut
    member: TeamMemberOut
