from app.schemas.admin_ops import AdminEventCreate


def test_create_event_syncs_from_min_max_members():
    body = AdminEventCreate(name="Flexible trio", team_min_size=3, team_max_size=4)
    assert body.team_min_size == 3
    assert body.team_max_size == 4
    assert body.required_member_count == 3
    assert body.substitute_count == 1
