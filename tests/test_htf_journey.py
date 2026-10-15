"""Unit tests for HTF journey and next_action pure calculation engine.

Runs purely in memory without requiring a database connection.
"""
from datetime import datetime, timezone
import pytest

from app.models.htf_application import HtfApplication, HtfApplicationStatus
from app.models.profile import Profile
from app.models.team import Team
from app.services.htf_dashboard_service import (
    HtfDashboardSnapshot,
    compute_journey_and_next_action,
    is_profile_complete,
)


def mock_profile(complete: bool = True) -> Profile:
    p = Profile()
    if complete:
        p.full_name = "Jane Doe"
        p.phone = "9876543210"
        p.college_name = "Tech Institute"
        p.department = "CSE"
        p.year_of_study = "3rd Year"
    else:
        p.full_name = "Jane Doe"
        p.phone = ""
        p.college_name = None
        p.department = None
        p.year_of_study = None
    return p


def mock_team() -> Team:
    t = Team()
    t.name = "Hackers"
    return t


def test_is_profile_complete():
    p_complete = mock_profile(complete=True)
    assert is_profile_complete(p_complete) is True

    p_incomplete = mock_profile(complete=False)
    assert is_profile_complete(p_incomplete) is False


def test_journey_incomplete_profile():
    now = datetime.now(timezone.utc)
    snapshot = HtfDashboardSnapshot(profile=mock_profile(False))
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "COMPLETE_PROFILE"
    assert next_action["action"] == "/profile"

    profile_stage = next(j for j in journey if j["key"] == "PROFILE")
    assert profile_stage["status"] == "IN_PROGRESS"

    team_stage = next(j for j in journey if j["key"] == "TEAM")
    assert team_stage["status"] == "LOCKED"


def test_journey_no_team():
    now = datetime.now(timezone.utc)
    snapshot = HtfDashboardSnapshot(profile=mock_profile(True), team=None)
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "CREATE_TEAM"
    assert next_action["action"] == "/team/create"

    team_stage = next(j for j in journey if j["key"] == "TEAM")
    assert team_stage["status"] == "IN_PROGRESS"


def test_journey_team_incomplete():
    now = datetime.now(timezone.utc)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=2,
        required_member_count=4,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "INVITE_TEAMMATES"
    assert "2 of 4 members have joined" in next_action["description"]

    team_stage = next(j for j in journey if j["key"] == "TEAM")
    assert team_stage["status"] == "IN_PROGRESS"


def test_journey_complete_team_no_app():
    now = datetime.now(timezone.utc)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=None,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "COMPLETE_APPLICATION"
    app_stage = next(j for j in journey if j["key"] == "APPLICATION")
    assert app_stage["status"] == "IN_PROGRESS"


def test_journey_draft_app_unsubmitted_all_answers_filled():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.DRAFT)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
        all_required_answers_filled=True,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "SUBMIT_APPLICATION"
    assert next_action["action"] == "/htf/application"


def test_journey_ppt_pending():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.PPT_PENDING)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "SUBMIT_PPT"
    ppt_stage = next(j for j in journey if j["key"] == "PPT")
    assert ppt_stage["status"] == "IN_PROGRESS"


def test_journey_under_screening():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.UNDER_SCREENING)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "WAIT_FOR_SCREENING"
    screening_stage = next(j for j in journey if j["key"] == "SCREENING")
    assert screening_stage["status"] == "IN_PROGRESS"


def test_journey_shortlisted_payment_pending():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.SHORTLISTED)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "PAY"
    payment_stage = next(j for j in journey if j["key"] == "PAYMENT")
    assert payment_stage["status"] == "IN_PROGRESS"


def test_journey_not_shortlisted():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.NOT_SHORTLISTED)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "NO_ACTION"
    payment_stage = next(j for j in journey if j["key"] == "PAYMENT")
    confirmation_stage = next(j for j in journey if j["key"] == "CONFIRMATION")
    assert payment_stage["status"] == "NOT_APPLICABLE"
    assert confirmation_stage["status"] == "NOT_APPLICABLE"


def test_journey_confirmed():
    now = datetime.now(timezone.utc)
    app = HtfApplication(status=HtfApplicationStatus.CONFIRMED)
    snapshot = HtfDashboardSnapshot(
        profile=mock_profile(True),
        team=mock_team(),
        active_member_count=4,
        required_member_count=4,
        application=app,
    )
    journey, next_action = compute_journey_and_next_action(snapshot, now)

    assert next_action["type"] == "VIEW_PASS"
    confirmation_stage = next(j for j in journey if j["key"] == "CONFIRMATION")
    assert confirmation_stage["status"] == "COMPLETE"
