from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_profile
from app.db.session import get_db
from app.models.profile import Profile
from app.schemas.htf_application import HtfApplicationOut
from app.schemas.htf_dashboard import HtfDashboardOut, HtfJourneyOut
from app.services import htf_dashboard_service

router = APIRouter(prefix="/htf", tags=["HTF Dashboard"])


@router.get(
    "/dashboard",
    response_model=HtfDashboardOut,
)
async def get_dashboard(
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Read-only dashboard status, journey, and priority next action."""
    snapshot = await htf_dashboard_service.load_dashboard_snapshot(db, profile)
    now = datetime.now(timezone.utc)
    journey, next_action = htf_dashboard_service.compute_journey_and_next_action(
        snapshot, now
    )

    event_dict = (
        {
            "id": str(snapshot.event.id),
            "name": snapshot.event.name,
            "visibility": snapshot.event.visibility,
        }
        if snapshot.event
        else None
    )

    profile_dict = {
        "id": str(profile.id),
        "full_name": profile.full_name,
        "is_complete": htf_dashboard_service.is_profile_complete(profile),
    }

    team_dict = (
        {
            "id": str(snapshot.team.id),
            "name": snapshot.team.name,
            "active_member_count": snapshot.active_member_count,
            "required_member_count": snapshot.required_member_count,
        }
        if snapshot.team
        else None
    )

    app_dict = None
    if snapshot.application:
        data = snapshot.application.application_data or {}
        app_dict = {
            "id": str(snapshot.application.id),
            "event_id": str(snapshot.application.event_id),
            "team_id": str(snapshot.application.team_id),
            "status": snapshot.application.status,
            "submitted_at": snapshot.application.submitted_at.isoformat()
            if snapshot.application.submitted_at
            else None,
            "responses": data.get("responses", {}),
            "is_editable": (
                snapshot.application.status
                == htf_dashboard_service.HtfApplicationStatus.DRAFT
            ),
        }

    return HtfDashboardOut(
        event=event_dict,
        profile=profile_dict,
        team=team_dict,
        application=app_dict,
        submission=snapshot.submission,
        screening=snapshot.screening,
        payment=snapshot.payment,
        confirmation=None,
        journey=journey,
        next_action=next_action,
        deadlines=snapshot.deadlines,
        announcements=[],  # TODO(Person4): announcements service when available
    )


@router.get(
    "/journey",
    response_model=HtfJourneyOut,
)
async def get_journey(
    profile: Profile = Depends(get_current_profile),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Read-only user journey and priority next action."""
    snapshot = await htf_dashboard_service.load_dashboard_snapshot(db, profile)
    now = datetime.now(timezone.utc)
    journey, next_action = htf_dashboard_service.compute_journey_and_next_action(
        snapshot, now
    )
    return HtfJourneyOut(journey=journey, next_action=next_action)
