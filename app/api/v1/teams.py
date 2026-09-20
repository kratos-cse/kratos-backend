import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps_stub import get_current_profile  # TODO: swap for real auth dep
from app.schemas.team import (
    InvitationOut,
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
def create_team(
    event_id: uuid.UUID,
    payload: TeamCreateRequest,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.create_team(db, event_id=event_id, profile=profile, name=payload.name)


@router.get("/teams/{team_id}", response_model=TeamDetailOut)
def get_team(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.get_team(db, team_id=team_id, profile=profile)


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
def list_team_members(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.list_team_members(db, team_id=team_id, profile=profile)


@router.patch("/teams/{team_id}", response_model=TeamDetailOut)
def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdateRequest,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.update_team(db, team_id=team_id, profile=profile, patch=payload)


@router.post("/teams/{team_id}/invitations", response_model=InvitationOut)
def create_invitation(
    team_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.create_invitation(db, team_id=team_id, profile=profile)