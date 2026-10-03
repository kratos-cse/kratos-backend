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

ROSTER_STYLE_FIXED = "FIXED"
ROSTER_STYLE_RANGE = "RANGE"
ROSTER_STYLE_MEMBERS_SUBSTITUTES = "MEMBERS_SUBSTITUTES"


def _rule_custom(rules: EventRegistrationRule) -> dict:
    raw = getattr(rules, "custom_fields", None)
    return raw if isinstance(raw, dict) else {}


def set_roster_style(rules: EventRegistrationRule, style: str) -> None:
    cf = dict(_rule_custom(rules))
    cf["roster_style"] = style
    rules.custom_fields = cf


def get_roster_style(rules: EventRegistrationRule) -> str:
    """Align with Admin-Frontend team_roster_style (FIXED | RANGE | MEMBERS_SUBSTITUTES).

    Admin always saves substitute_count=0 for FIXED/RANGE. Rows with substitute_count>0
    but a stale RANGE/FIXED style are normalized to MEMBERS_SUBSTITUTES.
    """
    subs = int(getattr(rules, "substitute_count", 0) or 0)
    style = _rule_custom(rules).get("roster_style")
    if style in (ROSTER_STYLE_FIXED, ROSTER_STYLE_RANGE, ROSTER_STYLE_MEMBERS_SUBSTITUTES):
        if style in (ROSTER_STYLE_FIXED, ROSTER_STYLE_RANGE) and subs > 0:
            return ROSTER_STYLE_MEMBERS_SUBSTITUTES
        return style
    if subs > 0:
        return ROSTER_STYLE_MEMBERS_SUBSTITUTES
    team_min, team_max = team_size_bounds(rules)
    if team_min == team_max:
        return ROSTER_STYLE_FIXED
    return ROSTER_STYLE_RANGE


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
    roster_style_value: str | None = None,
) -> None:
    style = roster_style_value or get_roster_style(rules)

    if style == ROSTER_STYLE_MEMBERS_SUBSTITUTES:
        req = max(
            1,
            min(
                MAX_REQUIRED_MEMBERS,
                int(required_member_count if required_member_count is not None else rules.required_member_count or 1),
            ),
        )
        subs = max(
            0,
            min(
                MAX_SUBSTITUTE_SLOTS,
                int(substitute_count if substitute_count is not None else rules.substitute_count or 0),
            ),
        )
        rules.required_member_count = req
        rules.substitute_count = subs
        rules.team_min_size = req
        rules.team_max_size = req + subs
        set_roster_style(rules, ROSTER_STYLE_MEMBERS_SUBSTITUTES)
        return

    if style == ROSTER_STYLE_FIXED:
        size = team_min_size if team_min_size is not None else team_max_size
        if size is None:
            size = required_member_count if required_member_count is not None else rules.team_min_size
        n = max(1, min(MAX_REQUIRED_MEMBERS, int(size or 1)))
        rules.team_min_size = n
        rules.team_max_size = n
        rules.required_member_count = n
        rules.substitute_count = 0
        set_roster_style(rules, ROSTER_STYLE_FIXED)
        return

    # RANGE (min–max flexible roster)
    mn = max(1, int(team_min_size if team_min_size is not None else rules.team_min_size or 1))
    mx = max(mn, int(team_max_size if team_max_size is not None else rules.team_max_size or mn))
    mn = min(MAX_REQUIRED_MEMBERS, mn)
    mx = min(MAX_REQUIRED_MEMBERS + MAX_SUBSTITUTE_SLOTS, mx)
    rules.team_min_size = mn
    rules.team_max_size = mx
    rules.required_member_count = mn
    rules.substitute_count = 0
    set_roster_style(rules, ROSTER_STYLE_RANGE)


def roster_limits(rules: EventRegistrationRule) -> tuple[int, int, int]:
    """Return (minimum_members, substitute_slots, maximum_members)."""
    team_min, team_max = team_size_bounds(rules)
    if get_roster_style(rules) == ROSTER_STYLE_MEMBERS_SUBSTITUTES:
        subs = max(0, int(rules.substitute_count or 0))
        return team_min, subs, team_max
    return team_min, 0, team_max


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
    """Fill mandatory seats, then optional members (RANGE) or substitute slots (MEMBERS+SUBS)."""
    style = get_roster_style(rules)
    team_min, team_max = team_size_bounds(rules)
    mandatory = count_mandatory(members)
    active = count_active(members)
    if mandatory < team_min:
        return TeamMemberRole.MEMBER
    if style == ROSTER_STYLE_MEMBERS_SUBSTITUTES:
        max_subs = max(0, int(rules.substitute_count or 0))
        if count_substitutes(members) < max_subs and active < team_max:
            return TeamMemberRole.SUBSTITUTE
        raise ValueError("roster_full")
    if active < team_max:
        return TeamMemberRole.MEMBER
    raise ValueError("roster_full")


def can_add_role(members: list[TeamMember], rules: EventRegistrationRule, role: TeamMemberRole) -> bool:
    style = get_roster_style(rules)
    team_min, team_max = team_size_bounds(rules)
    mandatory = count_mandatory(members)
    active = count_active(members)
    if role == TeamMemberRole.SUBSTITUTE:
        if style != ROSTER_STYLE_MEMBERS_SUBSTITUTES:
            return False
        max_subs = max(0, int(rules.substitute_count or 0))
        return mandatory >= team_min and count_substitutes(members) < max_subs and active < team_max
    if role in _MANDATORY_ROLES:
        if mandatory < team_min:
            return True
        if style == ROSTER_STYLE_MEMBERS_SUBSTITUTES:
            return False
        return active < team_max
    return False


def mandatory_met(members: list[TeamMember], rules: EventRegistrationRule) -> bool:
    team_min, _ = team_size_bounds(rules)
    return count_mandatory(members) >= team_min
