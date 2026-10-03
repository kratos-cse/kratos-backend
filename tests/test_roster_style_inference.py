"""Roster style matches Admin-Frontend team size models."""
from types import SimpleNamespace

from app.services.event_projection import _substitute_count
from app.services.roster_service import ROSTER_STYLE_MEMBERS_SUBSTITUTES, get_roster_style


def _rules(required, substitutes, team_min=None, team_max=None, roster_style=None):
    mn = team_min if team_min is not None else required
    mx = team_max if team_max is not None else required + substitutes
    cf = {"roster_style": roster_style} if roster_style else {}
    return SimpleNamespace(
        required_member_count=required,
        substitute_count=substitutes,
        team_min_size=mn,
        team_max_size=mx,
        custom_fields=cf,
    )


def test_members_substitutes_explicit_style():
    rules = _rules(5, 2, roster_style="MEMBERS_SUBSTITUTES")
    assert get_roster_style(rules) == ROSTER_STYLE_MEMBERS_SUBSTITUTES
    assert _substitute_count(rules) == 2


def test_legacy_range_with_substitutes_normalized():
    rules = _rules(6, 1, team_min=6, team_max=7, roster_style="RANGE")
    assert get_roster_style(rules) == ROSTER_STYLE_MEMBERS_SUBSTITUTES
    assert _substitute_count(rules) == 1


def test_range_has_zero_substitutes_in_api_projection():
    rules = _rules(3, 0, team_min=3, team_max=4, roster_style="RANGE")
    assert get_roster_style(rules) == "RANGE"
    assert _substitute_count(rules) == 0
