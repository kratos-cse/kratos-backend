"""Roster limits derived from EventRegistrationRule.

required_member_count + substitute_count is the explicit model.
team_min_size / team_max_size stay synced for legacy capacity checks.
"""
from __future__ import annotations

from app.models.enums import TeamMemberRole, TeamMemberStatus
from app.models.event import EventRegistrationRule
from app.models.team import TeamMember

_ACTIVE = (TeamMemberStatus.ACTIVE, TeamMemberStatus.PENDING_PAYMENT)
_MANDATORY_ROLES = (TeamMemberRole.LEADER, TeamMemberRole.MEMBER)


def sync_legacy_team_sizes(rules: EventRegistrationRule) -> None:
    """Keep team_min/max aligned with roster fields."""
    required = max(1, int(rules.required_member_count or 1))
    substitutes = max(0, int(rules.substitute_count or 0))
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
    Prefer explicit roster fields. If only legacy min/max provided, map:
      required = min, substitute = max(0, max - min)
    """
    if required_member_count is not None or substitute_count is not None:
        if required_member_count is not None:
            rules.required_member_count = required_member_count
        if substitute_count is not None:
            rules.substitute_count = substitute_count
    elif team_min_size is not None or team_max_size is not None:
        mn = team_min_size if team_min_size is not None else rules.team_min_size
        mx = team_max_size if team_max_size is not None else rules.team_max_size
        rules.required_member_count = max(1, int(mn))
        rules.substitute_count = max(0, int(mx) - int(mn))
    sync_legacy_team_sizes(rules)


def roster_limits(rules: EventRegistrationRule) -> tuple[int, int, int]:
    """Return (required, max_substitutes, total_roster)."""
    required = max(1, int(getattr(rules, "required_member_count", None) or rules.team_min_size or 1))
    substitutes = getattr(rules, "substitute_count", None)
    if substitutes is None:
        substitutes = max(0, int(rules.team_max_size or required) - required)
    else:
        substitutes = max(0, int(substitutes))
    return required, substitutes, required + substitutes


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
    """Assign MEMBER until mandatory filled, then SUBSTITUTE if slots remain."""
    required, max_subs, _ = roster_limits(rules)
    if count_mandatory(members) < required:
        return TeamMemberRole.MEMBER
    if count_substitutes(members) < max_subs:
        return TeamMemberRole.SUBSTITUTE
    raise ValueError("roster_full")


def can_add_role(members: list[TeamMember], rules: EventRegistrationRule, role: TeamMemberRole) -> bool:
    required, max_subs, _ = roster_limits(rules)
    if role == TeamMemberRole.SUBSTITUTE:
        return count_substitutes(members) < max_subs
    if role in _MANDATORY_ROLES:
        return count_mandatory(members) < required
    return False


def mandatory_met(members: list[TeamMember], rules: EventRegistrationRule) -> bool:
    required, _, _ = roster_limits(rules)
    return count_mandatory(members) >= required
