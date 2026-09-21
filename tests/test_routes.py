from app.main import app


def test_health_route_registered():
    paths = {route.path for route in app.routes}
    assert "/health" in paths


def test_api_v1_core_routes_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    expected = {
        "/api/v1/auth/google",
        "/api/v1/auth/me",
        "/api/v1/users/me/profile",
        "/api/v1/events",
        "/api/v1/events/{event_id}/teams",
        "/api/v1/teams/{team_id}",
        "/api/v1/team-invitations/{invite_code}",
        "/api/v1/team-invitations/{invite_code}/join",
        "/api/v1/payments/create-order",
        "/api/v1/payments/verify",
        "/api/v1/payments/webhook",
        "/api/v1/payments/{payment_id}",
        "/api/v1/admin/payments/{payment_id}/refund",
        "/api/v1/admin/me",
        "/api/v1/admin/roles",
        "/api/v1/admin/admin-users",
    }
    missing = expected - paths
    assert not missing, f"Missing routes: {missing}"


def test_no_supabase_import_in_payments():
    import app.payments as payments_pkg
    import importlib
    import pkgutil

    for mod in pkgutil.walk_packages(payments_pkg.__path__, payments_pkg.__name__ + "."):
        module = importlib.import_module(mod.name)
        assert "supabase" not in getattr(module, "__dict__", {}), mod.name
