"""Unit tests for roster limits and completion rules (5+2 model)."""
from types import SimpleNamespace

from app.models.enums import TeamMemberRole, TeamMemberStatus
from app.services.roster_service import (
    apply_roster_to_rules,
    can_add_role,
    count_mandatory,
    count_substitutes,
    mandatory_met,
    next_join_role,
    roster_limits,
    sync_legacy_team_sizes,
)


def _rules(required=5, substitutes=2, team_min=None, team_max=None):
    return SimpleNamespace(
        required_member_count=required,
        substitute_count=substitutes,
        team_min_size=team_min if team_min is not None else required,
        team_max_size=team_max if team_max is not None else required + substitutes,
    )


def _member(role, status=TeamMemberStatus.ACTIVE):
    return SimpleNamespace(role=role, status=status)


def test_roster_limits_5_plus_2():
    required, max_subs, total = roster_limits(_rules(5, 2))
    assert (required, max_subs, total) == (5, 2, 7)


def test_sync_legacy_from_roster_fields():
    rules = _rules(5, 2, team_min=1, team_max=1)
    sync_legacy_team_sizes(rules)
    assert rules.team_min_size == 5
    assert rules.team_max_size == 7


def test_apply_roster_prefers_explicit_fields():
    rules = _rules(1, 0, team_min=3, team_max=5)
    apply_roster_to_rules(rules, required_member_count=5, substitute_count=2)
    assert rules.required_member_count == 5
    assert rules.substitute_count == 2
    assert rules.team_min_size == 5
    assert rules.team_max_size == 7


def test_apply_roster_maps_legacy_min_max():
    rules = _rules(1, 0, team_min=1, team_max=1)
    apply_roster_to_rules(rules, team_min_size=4, team_max_size=6)
    assert rules.required_member_count == 4
    assert rules.substitute_count == 2
    assert rules.team_max_size == 6


def test_completion_5_plus_0_valid():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(4)
    ]
    assert mandatory_met(members, _rules(5, 2))
    assert count_substitutes(members) == 0


def test_completion_5_plus_1_valid():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(4)
    ] + [_member(TeamMemberRole.SUBSTITUTE)]
    assert mandatory_met(members, _rules(5, 2))
    assert count_substitutes(members) == 1


def test_completion_5_plus_2_valid():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(4)
    ] + [_member(TeamMemberRole.SUBSTITUTE) for _ in range(2)]
    assert mandatory_met(members, _rules(5, 2))
    assert count_substitutes(members) <= 2


def test_completion_4_plus_2_invalid():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(3)
    ] + [_member(TeamMemberRole.SUBSTITUTE) for _ in range(2)]
    assert not mandatory_met(members, _rules(5, 2))
    assert count_mandatory(members) == 4
    assert count_substitutes(members) == 2


def test_cannot_add_third_substitute():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(4)
    ] + [_member(TeamMemberRole.SUBSTITUTE) for _ in range(2)]
    assert not can_add_role(members, _rules(5, 2), TeamMemberRole.SUBSTITUTE)


def test_cannot_add_sixth_mandatory():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.MEMBER) for _ in range(4)
    ]
    assert not can_add_role(members, _rules(5, 2), TeamMemberRole.MEMBER)
    assert can_add_role(members, _rules(5, 2), TeamMemberRole.SUBSTITUTE)


def test_next_join_role_fills_mandatory_then_substitute():
    rules = _rules(5, 2)
    members = [_member(TeamMemberRole.LEADER)]
    assert next_join_role(members, rules) == TeamMemberRole.MEMBER
    members += [_member(TeamMemberRole.MEMBER) for _ in range(4)]
    assert next_join_role(members, rules) == TeamMemberRole.SUBSTITUTE


def test_substitutes_do_not_count_as_mandatory():
    members = [_member(TeamMemberRole.LEADER)] + [
        _member(TeamMemberRole.SUBSTITUTE) for _ in range(4)
    ]
    assert count_mandatory(members) == 1
    assert not mandatory_met(members, _rules(5, 2))
