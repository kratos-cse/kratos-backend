"""Public QR scanner capability, messages, and auth boundaries."""
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.enums import AttendanceScanResult, TeamMemberStatus
from app.services.scanner_service import member_matches_scope, public_scan_message
from app.services.scanner_capability import (
    SCANNER_CAPABILITY_MINUTES,
    SCANNER_TOKEN_AUDIENCE,
    SCANNER_TOKEN_PURPOSE,
    decode_team_capability,
    issue_team_capability,
)
from app.services.scanner_rate_limit import enforce_scanner_rate_limit, reset_scanner_rate_limit
def test_checkbox_scope_rejects_other_team_and_removed_members():
    team_id = uuid.uuid4()
    event_id = uuid.uuid4()
    assert member_matches_scope(
        member_team_id=team_id,
        member_event_id=event_id,
        member_status=TeamMemberStatus.ACTIVE,
        scope_team_id=team_id,
        scope_event_id=event_id,
    )
    assert not member_matches_scope(
        member_team_id=uuid.uuid4(),
        member_event_id=event_id,
        member_status=TeamMemberStatus.ACTIVE,
        scope_team_id=team_id,
        scope_event_id=event_id,
    )
    assert not member_matches_scope(
        member_team_id=team_id,
        member_event_id=uuid.uuid4(),
        member_status=TeamMemberStatus.ACTIVE,
        scope_team_id=team_id,
        scope_event_id=event_id,
    )
    assert not member_matches_scope(
        member_team_id=team_id,
        member_event_id=event_id,
        member_status=TeamMemberStatus.REMOVED,
        scope_team_id=team_id,
        scope_event_id=event_id,
    )


def test_capability_round_trip_and_scope():
    team_id = uuid.uuid4()
    event_id = uuid.uuid4()
    checkpoint_id = uuid.uuid4()
    token = issue_team_capability(team_id=team_id, event_id=event_id, checkpoint_id=checkpoint_id)
    scope = decode_team_capability(token)
    assert scope.team_id == team_id
    assert scope.event_id == event_id
    assert scope.checkpoint_id == checkpoint_id


def test_session_jwt_is_not_a_scanner_capability():
    now = datetime.now(timezone.utc)
    session = jwt.encode(
        {"sub": str(uuid.uuid4()), "jti": str(uuid.uuid4()), "iat": now, "exp": now + timedelta(minutes=5)},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(Exception):
        decode_team_capability(session)


def test_expired_capability_rejected():
    now = datetime.now(timezone.utc) - timedelta(minutes=SCANNER_CAPABILITY_MINUTES + 5)
    token = jwt.encode(
        {
            "purpose": SCANNER_TOKEN_PURPOSE,
            "aud": SCANNER_TOKEN_AUDIENCE,
            "team_id": str(uuid.uuid4()),
            "event_id": str(uuid.uuid4()),
            "checkpoint_id": str(uuid.uuid4()),
            "iat": now,
            "exp": now + timedelta(minutes=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(Exception) as exc:
        decode_team_capability(token)
    assert getattr(exc.value, "status_code", None) == 401


def test_public_messages():
    assert "Checked in" in public_scan_message(AttendanceScanResult.SUCCESS, None)
    assert "Already checked in" in public_scan_message(AttendanceScanResult.DUPLICATE, "repeat_scan")
    assert "inactive" in public_scan_message(AttendanceScanResult.INVALID, "inactive_qr").lower()
    assert "different event" in public_scan_message(AttendanceScanResult.INVALID, "wrong_event")
    assert "not eligible" in public_scan_message(AttendanceScanResult.NOT_PAID, None)


def test_rate_limit_blocks_after_threshold():
    reset_scanner_rate_limit()
    for _ in range(30):
        enforce_scanner_rate_limit("test-ip")
    with pytest.raises(Exception) as exc:
        enforce_scanner_rate_limit("test-ip")
    assert getattr(exc.value, "status_code", None) == 429
    reset_scanner_rate_limit()


def test_forged_member_checkin_and_admin_scan_stay_closed():
    client = TestClient(app_client())
    forged = client.post(
        "/api/v1/scanner/member-checkin",
        json={"capability": "not-a-real-token-value-xxxx", "team_member_id": str(uuid.uuid4())},
    )
    assert forged.status_code == 401
    admin_scan = client.post(
        "/api/v1/attendance/scan",
        json={"token": "abc", "checkpoint_id": str(uuid.uuid4())},
    )
    assert admin_scan.status_code == 401
    manual = client.post(
        "/api/v1/admin/attendance/manual",
        json={"checkpoint_id": str(uuid.uuid4()), "profile_id": str(uuid.uuid4())},
    )
    assert manual.status_code == 401


def app_client():
    from app.main import app

    return app
