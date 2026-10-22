from app.schemas.admin_ops import AdminEventCreate


def test_create_event_syncs_from_min_max_members():
    body = AdminEventCreate(name="Flexible trio", team_min_size=3, team_max_size=4)
    assert body.team_min_size == 3
    assert body.team_max_size == 4
    assert body.required_member_count == 3
    assert body.substitute_count == 0


def test_create_event_explicit_members_substitutes_preserved():
    body = AdminEventCreate(
        name="Five plus two",
        required_member_count=5,
        substitute_count=2,
        roster_style="MEMBERS_SUBSTITUTES",
    )
    assert body.required_member_count == 5
    assert body.substitute_count == 2
    assert body.team_min_size == 5
    assert body.team_max_size == 7
