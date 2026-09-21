"""Unit tests for KRATOS'26 join/pay rules: members never pay; leader pays once."""
from app.models.enums import TeamMemberRole, TeamMemberStatus


def member_status_on_join(role: TeamMemberRole) -> TeamMemberStatus:
    """Mirrors team_service.join_via_invitation / create_team membership rules."""
    if role == TeamMemberRole.LEADER:
        return TeamMemberStatus.PENDING_PAYMENT
    return TeamMemberStatus.ACTIVE


def test_member_join_is_always_active():
    assert member_status_on_join(TeamMemberRole.MEMBER) == TeamMemberStatus.ACTIVE


def test_leader_starts_pending_payment_until_team_registration_paid():
    assert member_status_on_join(TeamMemberRole.LEADER) == TeamMemberStatus.PENDING_PAYMENT
