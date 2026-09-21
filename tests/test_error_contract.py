from app.core.errors import TEAM_FULL, AppError
from app.main import app
from fastapi.testclient import TestClient


def test_error_handler_shape():
    @app.get("/__test_app_error")
    async def _boom():
        raise AppError(TEAM_FULL, "This team is already full.", status_code=409)

    client = TestClient(app)
    resp = client.get("/__test_app_error")
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == TEAM_FULL
    assert "full" in body["error"]["message"].lower()
