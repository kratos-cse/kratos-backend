from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_request_id_header():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")
    assert response.headers.get("X-Response-Time-Ms")


def test_client_request_id_is_echoed():
    client = TestClient(app)
    rid = "test-request-id-12345678"
    response = client.get("/health", headers={"X-Request-ID": rid})
    assert response.headers.get("X-Request-ID") == rid
