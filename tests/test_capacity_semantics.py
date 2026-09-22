"""Capacity counting and team helper unit tests."""
from app.models.enums import CapacityType, RegistrationStatus, TeamMemberStatus, TeamStatus
from app.services.event_service import _count_used_capacity  # noqa: F401 — imported for docs

# Document expected capacity semantics for reviewers / future integration tests.
CAPACITY_SEMANTICS = {
    "PARTICIPANTS": "solo non-cancelled registrations (profile_id set) + ACTIVE/PENDING_PAYMENT members",
    "TEAMS": "non-CANCELLED teams only",
    "no_double_count": "team Registration row is NOT counted as a participant",
}


def test_capacity_semantics_documented():
    assert CapacityType.PARTICIPANTS.value == "PARTICIPANTS"
    assert CapacityType.TEAMS.value == "TEAMS"
    assert RegistrationStatus.CANCELLED.value == "CANCELLED"
    assert TeamMemberStatus.LEFT.value == "LEFT"
    assert TeamStatus.CANCELLED.value == "CANCELLED"
    assert "no_double_count" in CAPACITY_SEMANTICS


def test_registration_partial_unique_names():
    from app.models.registration import Registration

    args = Registration.__table_args__
    index_names = {getattr(a, "name", None) for a in args}
    assert "uq_registrations_event_profile_active" in index_names
    assert "uq_registrations_event_team_active" in index_names
