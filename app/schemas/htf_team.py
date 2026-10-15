"""
Pydantic schemas for HTF 2026 – Member 2 domain.

Covers:
  • HTF Problem Statement (list / detail)
  • HTF Team Meta (readiness check, PS selection)
  • HTF Participant Pass
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    HTFApplicationStatus,
    HTFPassStatus,
    HTFProblemDomain,
    TeamMemberRole,
    TeamMemberStatus,
)


# ── Problem Statement schemas ─────────────────────────────────────────────────

class HTFProblemStatementOut(BaseModel):
    """Public-facing PS list item (shown to participants during team formation)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_id: uuid.UUID
    code: str
    title: str
    description: Optional[str] = None
    domain: HTFProblemDomain
    is_active: bool
    unique_claim: bool
    # How many teams have claimed this PS (returned by service, not model column)
    claim_count: int = 0


class HTFSelectPSRequest(BaseModel):
    """Payload when a team leader picks (or changes) their Problem Statement."""

    ps_id: uuid.UUID = Field(..., description="ID of the Problem Statement to select")


# ── Team Readiness schemas ─────────────────────────────────────────────────────

class HTFMemberReadiness(BaseModel):
    """Per-member breakdown of profile completeness."""

    profile_id: uuid.UUID
    full_name: str
    role: TeamMemberRole
    status: TeamMemberStatus
    is_profile_complete: bool
    # Which mandatory profile fields are missing (empty list = complete)
    missing_fields: list[str]


class HTFTeamReadinessOut(BaseModel):
    """
    Returned by GET /api/v1/htf/teams/me/readiness.

    `is_ready` is True only when ALL conditions pass:
      - team exists and is not cancelled
      - active member count is within [min_size, max_size]
      - every active member has a fully filled profile
      - team has not been withdrawn
    """

    team_id: uuid.UUID
    team_name: str
    event_id: uuid.UUID
    application_status: HTFApplicationStatus
    roster_locked: bool

    # Capacity checks
    active_member_count: int
    min_team_size: int
    max_team_size: int
    capacity_ok: bool

    # Profile completeness
    members: list[HTFMemberReadiness]
    all_profiles_complete: bool

    # PS selection
    ps_selected: bool
    ps_code: Optional[str] = None
    ps_title: Optional[str] = None
    ps_domain: Optional[HTFProblemDomain] = None

    # Final verdict
    is_ready: bool
    # Human-readable reasons why the team is NOT ready (empty when ready)
    blockers: list[str]


# ── Participant Pass schemas ────────────────────────────────────────────────────

class HTFPassMemberOut(BaseModel):
    """Condensed member info shown on the pass."""

    profile_id: uuid.UUID
    full_name: str
    role: TeamMemberRole
    college_name: Optional[str] = None
    department: Optional[str] = None
    year_of_study: Optional[str] = None
    # phone intentionally omitted from pass response for privacy;
    # only the QR token is needed for check-in
    contact_email: Optional[str] = None
    qr_token: Optional[str] = None       # None for members other than the requester
    team_member_id: uuid.UUID


class HTFPassOut(BaseModel):
    """
    Returned by GET /api/v1/htf/pass.

    The authenticated participant gets their own `qr_token` embedded here;
    teammate tokens are intentionally excluded (each person fetches their own pass).

    The front-end renders a digital pass / ticket using this payload.
    """

    pass_status: HTFPassStatus

    # Participant's own details
    my_profile_id: uuid.UUID
    my_full_name: str
    my_role: TeamMemberRole
    my_college: Optional[str] = None
    my_department: Optional[str] = None
    my_year: Optional[str] = None
    my_qr_token: Optional[str] = None   # Active QR token for check-in

    # Team details
    team_id: uuid.UUID
    team_name: str
    ps_code: Optional[str] = None
    ps_title: Optional[str] = None
    ps_domain: Optional[HTFProblemDomain] = None

    # Full roster (names only, no QR tokens for teammates)
    members: list[HTFPassMemberOut]

    # Event details
    event_id: uuid.UUID
    event_name: str
    event_venue: Optional[str] = None
    event_starts_at: Optional[datetime] = None
    event_ends_at: Optional[datetime] = None

    # HTF-specific lifecycle fields
    application_status: HTFApplicationStatus
    payment_deadline: Optional[datetime] = None
