from app.main import app
from app.services import audit_service


def test_audit_routes_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    expected = {
        "/api/v1/admin/audit-logs",
        "/api/v1/admin/audit-logs/user-journey/{profile_id}",
        "/api/v1/admin/audit-logs/payment/{payment_id}",
        "/api/v1/admin/notifications/{notification_id}/diagnostics",
    }
    missing = expected - paths
    assert not missing, f"Missing audit routes: {missing}"


def test_audit_log_activity_failsafe_without_db():
    # When db session cannot connect, log_activity must not raise.
    import asyncio

    async def _run():
        return await audit_service.log_activity(
            db=None,
            action="TEST_ACTION",
            resource_type="TEST",
            resource_id="x",
            status="SUCCESS",
            details={"ok": True},
        )

    # May return None if DATABASE_URL is unset — that is acceptable failsafe.
    entry = asyncio.get_event_loop().run_until_complete(_run())
    assert entry is None or entry.action == "TEST_ACTION"
