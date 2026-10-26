"""Legacy HTTPException detail → stable error code mapping."""

from app.core.errors import (
    ALREADY_REGISTERED,
    CONFLICT,
    ROSTER_INVALID,
    TEAM_FULL,
    TEAM_MANDATORY_FULL,
    TEAM_SUBSTITUTE_LIMIT,
    http_code_from_detail,
    MSG_ROSTER_INVALID_SUBSTITUTE,
    MSG_SUBSTITUTE_SLOTS_FULL,
    MSG_TEAM_ROSTER_FULL,
)


def test_409_team_full_from_participant_message():
    assert http_code_from_detail(409, MSG_TEAM_ROSTER_FULL) == TEAM_FULL


def test_409_substitute_slots_from_participant_message():
    assert http_code_from_detail(409, MSG_SUBSTITUTE_SLOTS_FULL) == TEAM_SUBSTITUTE_LIMIT


def test_409_roster_invalid_substitute():
    assert http_code_from_detail(409, MSG_ROSTER_INVALID_SUBSTITUTE) == ROSTER_INVALID


def test_409_already_registered_when_detail_mentions_already():
    assert http_code_from_detail(409, "You are already registered for this event") == ALREADY_REGISTERED


def test_409_unknown_detail_is_conflict_not_already_registered():
    assert http_code_from_detail(409, "Seat assignment race") == CONFLICT


def test_409_mandatory_full_legacy_detail():
    assert http_code_from_detail(409, "Mandatory roster is already full") == TEAM_MANDATORY_FULL
