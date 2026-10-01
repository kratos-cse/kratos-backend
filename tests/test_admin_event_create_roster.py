from app.schemas.admin_ops import AdminEventCreate


def test_create_event_syncs_team_sizes_from_required_members():
    body = AdminEventCreate(
        name="Quad event",
        required_member_count=4,
        substitute_count=1,
    )
    assert body.required_member_count == 4
    assert body.substitute_count == 1
    assert body.team_min_size == 4
    assert body.team_max_size == 5
