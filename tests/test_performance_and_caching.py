import time
import uuid
from app.api.deps_admin import _admin_cache, invalidate_admin_cache
from app.core.security import _user_cache, _profile_cache, invalidate_user_cache
from app.services.admin_ops_service import _dashboard_cache, invalidate_dashboard_cache
from app.services.event_service import _spots_cache, invalidate_spots_cache


def test_admin_cache_and_invalidation():
    uid = uuid.uuid4()
    _admin_cache[uid] = (time.monotonic() + 30.0, "mock_admin")
    assert uid in _admin_cache

    invalidate_admin_cache(uid)
    assert uid not in _admin_cache

    _admin_cache[uid] = (time.monotonic() + 30.0, "mock_admin")
    invalidate_admin_cache()
    assert len(_admin_cache) == 0


def test_user_and_profile_cache_and_invalidation():
    uid = uuid.uuid4()
    _user_cache[uid] = (time.monotonic() + 20.0, "mock_user")
    _profile_cache[uid] = (time.monotonic() + 20.0, "mock_profile")
    assert uid in _user_cache
    assert uid in _profile_cache

    invalidate_user_cache(uid)
    assert uid not in _user_cache
    assert uid not in _profile_cache


def test_dashboard_cache_and_invalidation():
    _dashboard_cache["payload"] = {"events_total": 10}
    _dashboard_cache["expires_at"] = time.monotonic() + 10.0
    assert _dashboard_cache["payload"] is not None

    invalidate_dashboard_cache()
    assert _dashboard_cache["payload"] is None
    assert _dashboard_cache["expires_at"] == 0.0


def test_spots_cache_and_invalidation():
    eid = uuid.uuid4()
    _spots_cache[eid] = (time.monotonic() + 5.0, 42)
    assert eid in _spots_cache

    invalidate_spots_cache(eid)
    assert eid not in _spots_cache
