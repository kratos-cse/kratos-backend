"""HTF Dashboard Service.

Snapshot loader + PURE journey & next_action computation engine.
Guarded optional imports for teammates' services (Person 3).
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.htf_errors import HTF_APPLICATION_NOT_OPEN
from app.models.enums import RegistrationFieldScope, TeamMemberStatus, TeamStatus
from app.models.event import Event
from app.models.event_content import EventRegistrationField
from app.models.htf_application import HtfApplication, HtfApplicationStatus
from app.models.profile import Profile
from app.models.team import Team, TeamMember
from app.services.field_response_validator import load_visible_fields
from app.services.htf_application_service import (
    get_htf_deadlines,
    get_htf_event,
    get_team_counts,
)

# TODO(Person3): Import submission/screening/payment services when available
try:
    from app.services import htf_submission_service
except ImportError:
    htf_submission_service = None

try:
    from app.services import htf_screening_service
except ImportError:
    htf_screening_service = None

try:
    from app.services import htf_payment_service
except ImportError:
    htf_payment_service = None


REQUIRED_PROFILE_FIELDS = (
    "full_name",
    "phone",
    "college_name",
    "department",
    "year_of_study",
)

NEXT_ACTION_ROUTES: dict[str, Optional[str]] = {
    "COMPLETE_PROFILE": "/profile",
    "CREATE_TEAM": "/team/create",
    "INVITE_TEAMMATES": "/team",
    "COMPLETE_APPLICATION": "/htf/application",
    "SUBMIT_APPLICATION": "/htf/application",
    "SUBMIT_PPT": "/htf/ppt",
    "WAIT_FOR_SCREENING": "/htf/dashboard",
    "PAY": "/htf/payment",
    "VIEW_CONFIRMATION": "/htf/confirmation",
    "VIEW_PASS": "/htf/pass",
    "NO_ACTION": None,
}


def is_profile_complete(profile: Optional[Profile]) -> bool:
    """Check if all required profile fields are non-empty."""
    if not profile:
        return False
    for field_name in REQUIRED_PROFILE_FIELDS:
        val = getattr(profile, field_name, None)
        if val is None or not str(val).strip():
            return False
    return True


@dataclass
class HtfDashboardSnapshot:
    event: Optional[Event] = None
    profile: Optional[Profile] = None
    team: Optional[Team] = None
    active_member_count: int = 0
    required_member_count: int = 1
    application: Optional[HtfApplication] = None
    submission: Optional[Any] = None
    screening: Optional[Any] = None
    payment: Optional[Any] = None
    deadlines: dict[str, Any] = field(default_factory=dict)
    all_required_answers_filled: bool = False


def compute_journey_and_next_action(
    snapshot: HtfDashboardSnapshot, now: datetime
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """
    PURE function (NO database access) computing user journey and next action.
    """
    profile_complete = is_profile_complete(snapshot.profile)
    has_team = snapshot.team is not None and snapshot.team.status != TeamStatus.CANCELLED
    team_complete = has_team and (snapshot.active_member_count >= snapshot.required_member_count)
    has_app = snapshot.application is not None
    app_status = snapshot.application.status if has_app else None

    # --- 1. Determine Journey Stages ---
    journey: list[dict[str, str]] = []

    # PROFILE
    profile_status = "COMPLETE" if profile_complete else "IN_PROGRESS"
    journey.append({"key": "PROFILE", "status": profile_status})

    # TEAM
    if not profile_complete:
        team_status = "LOCKED"
    elif team_complete:
        team_status = "COMPLETE"
    else:
        team_status = "IN_PROGRESS"
    journey.append({"key": "TEAM", "status": team_status})

    # APPLICATION
    if not team_complete:
        app_stage_status = "LOCKED"
    elif has_app and app_status != HtfApplicationStatus.DRAFT:
        app_stage_status = "COMPLETE"
    else:
        app_stage_status = "IN_PROGRESS"
    journey.append({"key": "APPLICATION", "status": app_stage_status})

    # PPT
    if app_status in (
        HtfApplicationStatus.PPT_SUBMITTED,
        HtfApplicationStatus.UNDER_SCREENING,
        HtfApplicationStatus.SHORTLISTED,
        HtfApplicationStatus.NOT_SHORTLISTED,
        HtfApplicationStatus.PAYMENT_PENDING,
        HtfApplicationStatus.CONFIRMED,
    ) or (snapshot.submission is not None):
        ppt_status = "COMPLETE"
    elif app_status == HtfApplicationStatus.PPT_PENDING:
        ppt_status = "IN_PROGRESS"
    else:
        ppt_status = "LOCKED"
    journey.append({"key": "PPT", "status": ppt_status})

    # SCREENING
    if app_status in (
        HtfApplicationStatus.SHORTLISTED,
        HtfApplicationStatus.NOT_SHORTLISTED,
        HtfApplicationStatus.PAYMENT_PENDING,
        HtfApplicationStatus.CONFIRMED,
    ):
        screening_status = "COMPLETE"
    elif app_status in (HtfApplicationStatus.PPT_SUBMITTED, HtfApplicationStatus.UNDER_SCREENING):
        screening_status = "IN_PROGRESS"
    else:
        screening_status = "LOCKED"
    journey.append({"key": "SCREENING", "status": screening_status})

    # PAYMENT
    if app_status == HtfApplicationStatus.NOT_SHORTLISTED:
        payment_status = "NOT_APPLICABLE"
    elif app_status == HtfApplicationStatus.CONFIRMED or (
        snapshot.payment is not None and getattr(snapshot.payment, "status", None) == "SUCCESS"
    ):
        payment_status = "COMPLETE"
    elif app_status in (HtfApplicationStatus.SHORTLISTED, HtfApplicationStatus.PAYMENT_PENDING):
        payment_status = "IN_PROGRESS"
    else:
        payment_status = "LOCKED"
    journey.append({"key": "PAYMENT", "status": payment_status})

    # CONFIRMATION
    if app_status == HtfApplicationStatus.NOT_SHORTLISTED:
        confirmation_status = "NOT_APPLICABLE"
    elif app_status == HtfApplicationStatus.CONFIRMED:
        confirmation_status = "COMPLETE"
    elif app_status in (HtfApplicationStatus.SHORTLISTED, HtfApplicationStatus.PAYMENT_PENDING):
        confirmation_status = "IN_PROGRESS"
    else:
        confirmation_status = "LOCKED"
    journey.append({"key": "CONFIRMATION", "status": confirmation_status})

    # --- 2. Determine Next Action ---
    if not profile_complete:
        next_action = {
            "type": "COMPLETE_PROFILE",
            "title": "Complete Your Profile",
            "description": "Please fill in all required profile details to proceed.",
            "action": NEXT_ACTION_ROUTES["COMPLETE_PROFILE"],
            "deadline": None,
        }
    elif not has_team:
        next_action = {
            "type": "CREATE_TEAM",
            "title": "Create or Join a Team",
            "description": "You need a team to participate in HTF 2026.",
            "action": NEXT_ACTION_ROUTES["CREATE_TEAM"],
            "deadline": None,
        }
    elif not team_complete:
        next_action = {
            "type": "INVITE_TEAMMATES",
            "title": "Invite Teammates",
            "description": f"{snapshot.active_member_count} of {snapshot.required_member_count} members have joined",
            "action": NEXT_ACTION_ROUTES["INVITE_TEAMMATES"],
            "deadline": None,
        }
    elif not has_app or app_status == HtfApplicationStatus.DRAFT:
        if snapshot.all_required_answers_filled:
            next_action = {
                "type": "SUBMIT_APPLICATION",
                "title": "Submit Application",
                "description": "All required fields are filled. Submit your application now.",
                "action": NEXT_ACTION_ROUTES["SUBMIT_APPLICATION"],
                "deadline": None,
            }
        else:
            next_action = {
                "type": "COMPLETE_APPLICATION",
                "title": "Complete Application",
                "description": "Fill out the HTF application form for your team.",
                "action": NEXT_ACTION_ROUTES["COMPLETE_APPLICATION"],
                "deadline": None,
            }
    elif app_status == HtfApplicationStatus.PPT_PENDING:
        next_action = {
            "type": "SUBMIT_PPT",
            "title": "Submit Pitch Deck (PPT)",
            "description": "Upload your project proposal / presentation deck.",
            "action": NEXT_ACTION_ROUTES["SUBMIT_PPT"],
            "deadline": snapshot.deadlines.get("ppt_deadline"),
        }
    elif app_status in (HtfApplicationStatus.PPT_SUBMITTED, HtfApplicationStatus.UNDER_SCREENING):
        next_action = {
            "type": "WAIT_FOR_SCREENING",
            "title": "Application Under Review",
            "description": "Your application and pitch deck are under review by judges.",
            "action": NEXT_ACTION_ROUTES["WAIT_FOR_SCREENING"],
            "deadline": None,
        }
    elif app_status in (HtfApplicationStatus.SHORTLISTED, HtfApplicationStatus.PAYMENT_PENDING):
        next_action = {
            "type": "PAY",
            "title": "Complete Payment",
            "description": "Your team has been shortlisted! Pay the registration fee to confirm.",
            "action": NEXT_ACTION_ROUTES["PAY"],
            "deadline": None,
        }
    elif app_status == HtfApplicationStatus.NOT_SHORTLISTED:
        next_action = {
            "type": "NO_ACTION",
            "title": "Application Status",
            "description": "Thank you for applying. Unfortunately your team was not shortlisted.",
            "action": NEXT_ACTION_ROUTES["NO_ACTION"],
            "deadline": None,
        }
    elif app_status == HtfApplicationStatus.CONFIRMED:
        next_action = {
            "type": "VIEW_PASS",
            "title": "Registration Confirmed",
            "description": "Your team is fully confirmed for HTF 2026. View your entry pass.",
            "action": NEXT_ACTION_ROUTES["VIEW_PASS"],
            "deadline": None,
        }
    else:
        next_action = {
            "type": "NO_ACTION",
            "title": "No Action Required",
            "description": "No pending actions at this time.",
            "action": NEXT_ACTION_ROUTES["NO_ACTION"],
            "deadline": None,
        }

    return journey, next_action


async def load_dashboard_snapshot(
    db: AsyncSession, profile: Profile
) -> HtfDashboardSnapshot:
    """Gather snapshot dataclass for the caller from DB."""
    try:
        event = await get_htf_event(db)
    except Exception:
        event = None

    deadlines = await get_htf_deadlines(db, event) if event else {}

    team = None
    active_count = 0
    required_count = 1
    application = None
    all_required_filled = False

    if event:
        # Find active team member entry for caller
        m_res = await db.execute(
            select(TeamMember)
            .where(
                TeamMember.event_id == event.id,
                TeamMember.profile_id == profile.id,
                TeamMember.status.notin_((TeamMemberStatus.LEFT, TeamMemberStatus.REMOVED)),
            )
        )
        member = m_res.scalar_one_or_none()
        if member:
            t_res = await db.execute(select(Team).where(Team.id == member.team_id))
            team = t_res.scalar_one_or_none()
            if team:
                active_count, required_count = await get_team_counts(db, team.id)

                app_res = await db.execute(
                    select(HtfApplication).where(
                        HtfApplication.event_id == event.id,
                        HtfApplication.team_id == team.id,
                    )
                )
                application = app_res.scalar_one_or_none()

        # Check required registration fields
        if application:
            stored_resp = (application.application_data or {}).get("responses", {})
            visible_fields = await load_visible_fields(
                db, event.id, RegistrationFieldScope.REGISTRATION
            )
            req_fields = [f for f in visible_fields if f.required]
            if req_fields:
                all_required_filled = all(
                    str(f.id) in stored_resp and stored_resp[str(f.id)] is not None
                    for f in req_fields
                )
            else:
                all_required_filled = True

    # Guarded optional loads from Person 3 modules
    submission = None
    if htf_submission_service and team and hasattr(htf_submission_service, "get_submission"):
        try:
            submission = await htf_submission_service.get_submission(db, team.id)
        except Exception:
            pass

    screening = None
    if htf_screening_service and team and hasattr(htf_screening_service, "get_screening"):
        try:
            screening = await htf_screening_service.get_screening(db, team.id)
        except Exception:
            pass

    payment = None
    if htf_payment_service and team and hasattr(htf_payment_service, "get_payment"):
        try:
            payment = await htf_payment_service.get_payment(db, team.id)
        except Exception:
            pass

    return HtfDashboardSnapshot(
        event=event,
        profile=profile,
        team=team,
        active_member_count=active_count,
        required_member_count=required_count,
        application=application,
        submission=submission,
        screening=screening,
        payment=payment,
        deadlines=deadlines,
        all_required_answers_filled=all_required_filled,
    )
