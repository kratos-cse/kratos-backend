"""Public event projection exposes roster_style and team size bounds for registration UI."""
from types import SimpleNamespace

from app.schemas.event import EventDetail, EventListItem
from app.services.event_projection import build_event_state_from_remaining
from app.services.roster_service import (
    ROSTER_STYLE_MEMBERS_SUBSTITUTES,
    ROSTER_STYLE_RANGE,
    apply_roster_to_rules,
    get_roster_style,
)
from app.models.event import EventRegistrationRule
import uuid


def _event():
    return SimpleNamespace(
        visibility="PUBLISHED",
        registration_status="OPEN",
    )


def _rules(**kwargs):
    rules = EventRegistrationRule(
        event_id=kwargs.get("event_id"),
        team_min_size=kwargs.get("team_min_size", 1),
        team_max_size=kwargs.get("team_max_size", 1),
        required_member_count=kwargs.get("required_member_count", 1),
        substitute_count=kwargs.get("substitute_count", 0),
        custom_fields=kwargs.get("custom_fields"),
        allow_individual=True,
        registration_mode="TEAM_ONLY",
    )
    return rules


def test_range_3_4_projection():
    rules = _rules(team_min_size=3, team_max_size=4)
    apply_roster_to_rules(
        rules,
        roster_style_value="RANGE",
        team_min_size=3,
        team_max_size=4,
    )
    state = build_event_state_from_remaining(_event(), rules, 50)
    assert state["team_min_size"] == 3
    assert state["team_max_size"] == 4
    assert state["required_member_count"] == 3
    assert state["substitute_count"] == 0
    assert state["roster_style"] == "RANGE"
    assert get_roster_style(rules) == "RANGE"


def test_members_substitutes_projection_zeros_public_substitute_count_for_range():
    rules = _rules(team_min_size=3, team_max_size=4, substitute_count=1)
    apply_roster_to_rules(rules, roster_style_value="RANGE", team_min_size=3, team_max_size=4)
    state = build_event_state_from_remaining(_event(), rules, 50)
    assert state["substitute_count"] == 0
    assert state["team_max_size"] == 4


def test_projection_roster_style_always_matches_get_roster_style():
    rules = _rules(team_min_size=5, team_max_size=7, substitute_count=2)
    apply_roster_to_rules(
        rules,
        roster_style_value=ROSTER_STYLE_MEMBERS_SUBSTITUTES,
        required_member_count=5,
        substitute_count=2,
    )
    state = build_event_state_from_remaining(_event(), rules, 10)
    assert state["roster_style"] == get_roster_style(rules)
    assert state["roster_style"] == ROSTER_STYLE_MEMBERS_SUBSTITUTES
    assert state["substitute_count"] == 2


def test_legacy_stale_range_label_with_substitute_count_normalizes_in_projection():
    """Stale DB: roster_style RANGE + substitute_count>0 is treated as MEMBERS_SUBSTITUTES."""
    rules = _rules(
        team_min_size=3,
        team_max_size=4,
        substitute_count=1,
        custom_fields={"roster_style": ROSTER_STYLE_RANGE},
    )
    assert get_roster_style(rules) == ROSTER_STYLE_MEMBERS_SUBSTITUTES
    state = build_event_state_from_remaining(_event(), rules, 10)
    assert state["roster_style"] == ROSTER_STYLE_MEMBERS_SUBSTITUTES
    assert state["substitute_count"] == 1
    assert state["team_max_size"] == 4


def test_event_detail_schema_serializes_roster_style_from_projection():
    rules = _rules(team_min_size=3, team_max_size=4)
    apply_roster_to_rules(rules, roster_style_value=ROSTER_STYLE_RANGE, team_min_size=3, team_max_size=4)
    state = build_event_state_from_remaining(_event(), rules, 5)
    base = {
        "id": uuid.uuid4(),
        "name": "Test",
        "short_desc": None,
        "long_desc": None,
        "category": None,
        "fee": 0,
        "venue": None,
        "capacity": None,
        "starts_at": None,
        "ends_at": None,
        "slot": None,
        "visibility": "PUBLISHED",
        "registration_status": "OPEN",
        "capacity_type": None,
        "member_registration_mode": None,
        "allow_team_invite_flow": False,
        "requires_qr_checkin": True,
        **state,
    }
    detail = EventDetail(**base)
    dumped = detail.model_dump()
    assert dumped["roster_style"] == ROSTER_STYLE_RANGE
    assert dumped["team_max_size"] == 4

    list_item = EventListItem(
        id=base["id"],
        name=base["name"],
        short_desc=base["short_desc"],
        category=base["category"],
        fee=base["fee"],
        venue=base["venue"],
        starts_at=base["starts_at"],
        ends_at=base["ends_at"],
        slot=base["slot"],
        visibility=base["visibility"],
        registration_status=base["registration_status"],
        registration_open=state["registration_open"],
        registration_availability=state["registration_availability"],
        spots_remaining=state["spots_remaining"],
        allow_individual=state["allow_individual"],
        registration_mode=state["registration_mode"],
        team_min_size=state["team_min_size"],
        team_max_size=state["team_max_size"],
        required_member_count=state["required_member_count"],
        substitute_count=state["substitute_count"],
        roster_style=state["roster_style"],
    )
    assert list_item.model_dump()["roster_style"] == ROSTER_STYLE_RANGE
