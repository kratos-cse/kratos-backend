from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps_stub import get_current_profile  # TODO: swap for real auth dep
from app.schemas.team import InvitationPublicOut, JoinTeamResponse
from app.services import team_service

router = APIRouter(tags=["Team Invitations"])


@router.get("/team-invitations/{invite_code}", response_model=InvitationPublicOut)
def get_invitation(invite_code: str, db: Session = Depends(get_db)):
    return team_service.get_invitation_public(db, invite_code=invite_code)


@router.post("/team-invitations/{invite_code}/join", response_model=JoinTeamResponse)
def join_team(
    invite_code: str,
    db: Session = Depends(get_db),
    profile=Depends(get_current_profile),
):
    return team_service.join_via_invitation(db, invite_code=invite_code, profile=profile)