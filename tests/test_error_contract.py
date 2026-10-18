from app.core.errors import TEAM_FULL, AppError
from app.main import app
from fastapi.testclient import TestClient


def test_error_handler_shape():
    @app.get("/__test_app_error")
    async def _boom():
        raise AppError(TEAM_FULL, "This team is already full.", status_code=409)

    client = TestClient(app)
    resp = client.get("/__test_app_error", headers={"X-Request-ID": "participant-test-req-1"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == TEAM_FULL
    assert "full" in body["error"]["message"].lower()
    assert body["error"]["request_id"] == "participant-test-req-1"
    assert "traceback" not in resp.text.lower()


def test_http_exception_maps_to_error_envelope():
    @app.get("/__test_http_error")
    async def _http():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Event not found")

    client = TestClient(app)
    resp = client.get("/__test_http_error")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "EVENT_NOT_FOUND"
    assert "event" in body["error"]["message"].lower()
