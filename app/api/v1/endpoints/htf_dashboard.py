from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User
from app.api.deps_auth import get_current_user

router = APIRouter(prefix="/htf", tags=["HTF Participant Dashboard"])

@router.get("/dashboard")
async def get_htf_dashboard(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full HTF dashboard orchestration data:
    Profile, Team, Application, Submission, Screening, Payment, Confirmation, and Deadlines.
    """
    # TODO: Orchestrate and fetch all required HTF state for the participant.
    # This requires models built by Person 1, 2, and 3.
    return {
        "status": "success",
        "data": {
            "profile": {},
            "team": {},
            "application": {},
            "submission": {},
            "screening": {},
            "payment": {},
            "confirmation": {},
            "journey": [],
            "next_action": {},
            "deadlines": [],
            "announcements": []
        }
    }

@router.get("/journey")
async def get_htf_journey(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the HTF state machine journey for the participant.
    """
    # TODO: Calculate journey based on HTF application and payment status
    return {
        "status": "success",
        "data": []
    }

@router.get("/pass")
async def get_participant_pass(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the participant's event pass (QR reference, venue, timing, confirmation).
    Should only return if payment is confirmed.
    """
    # TODO: Verify HTF application is CONFIRMED and payment is PAID.
    # TODO: Fetch QR reference from team member.
    return {
        "status": "success",
        "data": {
            "participant_name": "TODO",
            "team_name": "TODO",
            "confirmation_status": "TODO",
            "qr_reference": "TODO",
            "event_timing": "TODO",
            "venue": "TODO"
        }
    }
