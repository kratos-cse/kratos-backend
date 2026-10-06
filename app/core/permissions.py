"""Admin permission keys and backward-compatible aliases."""

# New granular permissions (Part 3 spec)
EVENT_READ = "event-read"
EVENT_EDIT = "event-edit"
EVENT_CONTROL = "event-control"
REGISTRATION_READ = "registration-read"
REGISTRATION_EDIT = "registration-edit"
TEAM_READ = "team-read"
TEAM_EDIT = "team-edit"
PARTICIPANT_READ = "participant-read"
PARTICIPANT_EDIT = "participant-edit"
PAYMENT_READ = "payment-read"
ATTENDANCE_READ = "attendance-read"
EXPORT = "export"
EVENT_ASSIGNMENT_MANAGEMENT = "event-assignment-management"
ADMIN_MANAGEMENT = "admin-management"
ROLE_MANAGEMENT = "role-management"
DASHBOARD = "dashboard"

# Legacy keys retained for compatibility
LEGACY_EVENT_MANAGEMENT = "event-management"
LEGACY_EVENT_RULE_MANAGEMENT = "event-rule-management"

# Maps requested permission -> legacy keys that also satisfy it
PERMISSION_ALIASES: dict[str, tuple[str, ...]] = {
    EVENT_READ: (LEGACY_EVENT_MANAGEMENT, EVENT_EDIT, EVENT_CONTROL),
    EVENT_EDIT: (LEGACY_EVENT_MANAGEMENT, LEGACY_EVENT_RULE_MANAGEMENT),
    EVENT_CONTROL: (LEGACY_EVENT_MANAGEMENT,),
    DASHBOARD: (),
}

EVENT_COORDINATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
        EVENT_READ,
        REGISTRATION_READ,
        TEAM_READ,
        PARTICIPANT_READ,
        PAYMENT_READ,
        DASHBOARD,
    }
)


def expand_permission_keys(keys: set[str]) -> set[str]:
    """Return keys plus any permissions implied by legacy aliases held by the admin."""
    expanded = set(keys)
    for perm, aliases in PERMISSION_ALIASES.items():
        if perm in keys or any(a in keys for a in aliases):
            expanded.add(perm)
    return expanded


def admin_has_expanded_permission(keys: set[str], *required: str) -> bool:
    expanded = expand_permission_keys(keys)
    return any(r in expanded for r in required)
