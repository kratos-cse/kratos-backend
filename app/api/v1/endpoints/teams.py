import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.profile import Profile
from app.schemas.team import (
    InvitationOut,
    InvitationPublicOut,
    JoinTeamResponse,
    TeamCreateRequest,
    TeamDetailOut,
    TeamMemberOut,
    TeamUpdateRequest,
)
from app.services import team_service

router = APIRouter(tags=["Teams"])


@router.post(
    "/events/{event_id}/teams",
    response_model=TeamDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    event_id: uuid.UUID,
    payload: TeamCreateRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.create_team(db, event_id=event_id, profile=profile, name=payload.name)


@router.get("/teams/{team_id}", response_model=TeamDetailOut)
async def get_team(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.get_team(db, team_id=team_id, profile=profile)


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
async def list_team_members(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.list_team_members(db, team_id=team_id, profile=profile)


@router.patch("/teams/{team_id}", response_model=TeamDetailOut)
async def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdateRequest,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.update_team(db, team_id=team_id, profile=profile, patch=payload)


@router.post("/teams/{team_id}/invitations", response_model=InvitationOut)
async def create_invitation(
    team_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.create_invitation(db, team_id=team_id, profile=profile)


@router.get("/team-invitations/{invite_code}", response_model=InvitationPublicOut)
async def get_invitation(invite_code: str, db: AsyncSession = Depends(get_db)):
    return await team_service.get_invitation_public(db, invite_code=invite_code)


@router.post("/team-invitations/{invite_code}/join", response_model=JoinTeamResponse)
async def join_team(
    invite_code: str,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.join_via_invitation(db, invite_code=invite_code, profile=profile)


@router.post("/teams/{team_id}/members/{member_id}/leave", response_model=TeamMemberOut)
async def leave_team(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.leave_team(db, team_id=team_id, member_id=member_id, profile=profile)


@router.post("/teams/{team_id}/members/{member_id}/remove", response_model=TeamMemberOut)
async def remove_member(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    profile: Profile = Depends(get_current_profile),
):
    return await team_service.remove_member(db, team_id=team_id, member_id=member_id, profile=profile)
