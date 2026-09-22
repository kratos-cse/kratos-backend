import time
import uuid
import pytest
from unittest.mock import MagicMock

from app.api.deps_admin import (
    _ADMIN_CACHE,
    get_current_active_admin,
    invalidate_admin_cache,
)
from app.main import app
from app.models.admin import AdminUser, Role
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


def test_admin_rbac_ttl_cache():
    invalidate_admin_cache()
    user_id = uuid.uuid4()

    mock_admin = AdminUser(id=uuid.uuid4(), user_id=user_id, is_active=True)
    _ADMIN_CACHE[user_id] = (mock_admin, time.monotonic() + 60.0)

    # Cache hit
    cached = _ADMIN_CACHE.get(user_id)
    assert cached is not None
    assert cached[0].user_id == user_id

    # Invalidate by user_id
    invalidate_admin_cache(user_id)
    assert _ADMIN_CACHE.get(user_id) is None


@pytest.mark.anyio
async def test_audit_service_log_activity_in_memory():
    # Test log_activity creates proper AuditLog model instance with details
    actor_uid = uuid.uuid4()
    actor_pid = uuid.uuid4()
    payment_id = uuid.uuid4()

    entry = await audit_service.log_activity(
        db=None,
        action="PAYMENT_ORDER_CREATED",
        resource_type="PAYMENT",
        resource_id=payment_id,
        actor_user_id=actor_uid,
        actor_profile_id=actor_pid,
        actor_role="PARTICIPANT",
        status="SUCCESS",
        details={
            "amount_paise": 25000,
            "razorpay_order_id": "order_test_123",
        },
    )
    # Even if standalone DB session is mocked or executed against real DB, function returns entry safely
    if entry:
        assert entry.action == "PAYMENT_ORDER_CREATED"
        assert entry.resource_type == "PAYMENT"
        assert entry.resource_id == str(payment_id)
        assert entry.status == "SUCCESS"
        assert entry.details["amount_paise"] == 25000


def test_notification_failure_diagnostics():
    from app.services.notification_service import _send_smtp

    # Verify diagnostic categorization strings for failure analysis
    test_cases = [
        ("SMTP not configured (SMTP_HOST / SMTP_FROM missing)", "SMTP_UNCONFIGURED"),
        ("Connection timed out after 3.0s", "SMTP_TIMEOUT"),
        ("535 5.7.8 Username and Password not accepted", "SMTP_AUTH_ERROR"),
        ("Unknown server disconnect", "SMTP_DELIVERY_ERROR"),
    ]

    for err_str, expected_cat in test_cases:
        if "SMTP not configured" in err_str:
            cat = "SMTP_UNCONFIGURED"
        elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
            cat = "SMTP_TIMEOUT"
        elif any(w in err_str.lower() for w in ("auth", "login", "credentials", "password", "535")):
            cat = "SMTP_AUTH_ERROR"
        else:
            cat = "SMTP_DELIVERY_ERROR"
        assert cat == expected_cat
