import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import TeamMemberRole, TeamMemberStatus, TeamStatus


# ---------- Requests ----------

class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TeamUpdateRequest(BaseModel):
    # Only fields an owner/admin may edit via PATCH /teams/{id}.
    # status is deliberately excluded - status transitions are
    # internal-only (payment, capacity, admin actions), never
    # client-supplied.
    name: str | None = Field(default=None, min_length=1, max_length=200)


# ---------- Responses ----------

class TeamMemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    profile_id: uuid.UUID
    role: TeamMemberRole
    status: TeamMemberStatus
    joined_at: datetime
    full_name: str | None = None  # filled in by the service from PROFILES


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
    """Returned by the public GET /team-invitations/{code} - deliberately
    excludes anything sensitive (no emails/phones)."""

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
    requires_payment: bool  # True when fee_charge_model is PER_MEMBER