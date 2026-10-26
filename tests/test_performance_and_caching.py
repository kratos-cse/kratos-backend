import time
import uuid

from app.api.deps_admin import _admin_cache, invalidate_admin_cache
from app.core.security import invalidate_user_cache
from app.services.admin_ops_service import _dashboard_cache, invalidate_dashboard_cache
from app.services.event_service import _spots_cache, invalidate_spots_cache


def test_admin_cache_invalidation():
    uid = uuid.uuid4()
    _admin_cache[uid] = (time.monotonic() + 30.0, "mock")
    invalidate_admin_cache(uid)
    assert uid not in _admin_cache


def test_user_profile_cache_invalidation_is_noop_safe():
    """ORM auth cache removed; invalidate_user_cache remains callable for profile PATCH."""
    invalidate_user_cache(uuid.uuid4())
    invalidate_user_cache(None)


def test_dashboard_cache_invalidation():
    _dashboard_cache["payload"] = {"events_total": 1}
    _dashboard_cache["expires_at"] = time.monotonic() + 10.0
    invalidate_dashboard_cache()
    assert _dashboard_cache["payload"] is None


def test_spots_cache_invalidation():
    eid = uuid.uuid4()
    _spots_cache[eid] = (time.monotonic() + 5.0, 3)
    invalidate_spots_cache(eid)
    assert eid not in _spots_cache
