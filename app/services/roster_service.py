"""Roster limits derived from EventRegistrationRule.

Per-event team size is team_min_size .. team_max_size (inclusive, leader counts).
required_member_count mirrors team_min_size; substitute_count = max - min for legacy fields.
"""
from __future__ import annotations

from app.models.enums import TeamMemberRole, TeamMemberStatus
from app.models.event import EventRegistrationRule
from app.models.team import TeamMember

_ACTIVE = (TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT)
_MANDATORY_ROLES = (TeamMemberRole.LEADER, TeamMemberRole.MEMBER)

# Upper bounds for organizer-configured rosters (leader + teammates + substitutes).
MAX_REQUIRED_MEMBERS = 30
MAX_SUBSTITUTE_SLOTS = 20


def team_size_bounds(rules: EventRegistrationRule) -> tuple[int, int]:
    team_min = max(1, min(MAX_REQUIRED_MEMBERS, int(rules.team_min_size or rules.required_member_count or 1)))
    team_max = max(
        team_min,
        min(MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS, int(rules.team_max_size or team_min)),
    )
    return team_min, team_max


def count_active(members: list[TeamMember]) -> int:
    return sum(1 for m in members if m.status in _ACTIVE)


def sync_legacy_team_sizes(rules: EventRegistrationRule) -> None:
    """Keep team_min/max aligned with roster fields."""
    required = max(1, min(MAX_REQUIRED_MEMBERS, int(rules.required_member_count or 1)))
    substitutes = max(0, min(MAX_SUBSTITUTE_SLOTS, int(rules.substitute_count or 0)))
    rules.required_member_count = required
    rules.substitute_count = substitutes
    rules.team_min_size = required
    rules.team_max_size = required + substitutes


def apply_roster_to_rules(
    rules: EventRegistrationRule,
    *,
    required_member_count: int | None = None,
    substitute_count: int | None = None,
    team_min_size: int | None = None,
    team_max_size: int | None = None,
) -> None:
    """
    Prefer team_min_size / team_max_size when provided. Legacy required + substitute_count
    still supported for API clients.
    """
    if team_min_size is not None or team_max_size is not None:
        mn = max(1, int(team_min_size if team_min_size is not None else rules.team_min_size or 1))
        mx = max(mn, int(team_max_size if team_max_size is not None else rules.team_max_size or mn))
        mn = min(MAX_REQUIRED_MEMBERS, mn)
        mx = min(MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS, mx)
        rules.team_min_size = mn
        rules.team_max_size = mx
        rules.required_member_count = mn
        rules.substitute_count = max(0, mx - mn)
    elif required_member_count is not None or substitute_count is not None:
        if required_member_count is not None:
            rules.required_member_count = required_member_count
        if substitute_count is not None:
            rules.substitute_count = substitute_count
        sync_legacy_team_sizes(rules)


def roster_limits(rules: EventRegistrationRule) -> tuple[int, int, int]:
    """Return (minimum_members, optional_slots, maximum_members)."""
    team_min, team_max = team_size_bounds(rules)
    return team_min, max(0, team_max - team_min), team_max


def count_mandatory(members: list[TeamMember]) -> int:
    return sum(
        1
        for m in members
        if m.status in _ACTIVE and m.role in _MANDATORY_ROLES
    )


def count_substitutes(members: list[TeamMember]) -> int:
    return sum(
        1
        for m in members
        if m.status in _ACTIVE and m.role == TeamMemberRole.SUBSTITUTE
    )


def next_join_role(members: list[TeamMember], rules: EventRegistrationRule) -> TeamMemberRole:
    """Fill mandatory seats up to team_min, then optional members up to team_max."""
    team_min, team_max = team_size_bounds(rules)
    mandatory = count_mandatory(members)
    active = count_active(members)
    if mandatory < team_min:
        return TeamMemberRole.MEMBER
    if active < team_max:
        return TeamMemberRole.MEMBER
    _, max_subs, total = roster_limits(rules)
    if count_substitutes(members) < max_subs and active < total + max_subs:
        return TeamMemberRole.SUBSTITUTE
    raise ValueError("roster_full")


def can_add_role(members: list[TeamMember], rules: EventRegistrationRule, role: TeamMemberRole) -> bool:
    team_min, team_max = team_size_bounds(rules)
    mandatory = count_mandatory(members)
    active = count_active(members)
    if role == TeamMemberRole.SUBSTITUTE:
        _, max_subs, _ = roster_limits(rules)
        return count_substitutes(members) < max_subs
    if role in _MANDATORY_ROLES:
        if mandatory < team_min:
            return True
        return active < team_max
    return False


def mandatory_met(members: list[TeamMember], rules: EventRegistrationRule) -> bool:
    team_min, _ = team_size_bounds(rules)
    return count_mandatory(members) >= team_min
