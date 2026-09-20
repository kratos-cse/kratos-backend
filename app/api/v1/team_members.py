import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps_stub import get_current_profile  # TODO: swap for real auth dep
from app.schemas.team import TeamMemberOut
from app.services import team_service

router = APIRouter(tags=["Team Members"])


@router.post("/teams/{team_id}/members/{member_id}/leave", response_model=TeamMemberOut)
def leave_team(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.leave_team(db, team_id=team_id, member_id=member_id, profile=profile)


@router.post("/teams/{team_id}/members/{member_id}/remove", response_model=TeamMemberOut)
def remove_member(
    team_id: uuid.UUID,
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.remove_member(db, team_id=team_id, member_id=member_id, profile=profile)