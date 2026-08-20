from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok_no_auth() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_does_not_require_authorization_header() -> None:
    resp = client.get("/api/health", headers={})
    assert resp.status_code == 200
