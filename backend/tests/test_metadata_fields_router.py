import uuid

import pytest
from fastapi.testclient import TestClient

from app.deps import CurrentUser, get_current_user, get_db
from app.main import app
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


def _current_user() -> CurrentUser:
    return CurrentUser(id=USER_ID, email="user@example.com", claims={})


@pytest.fixture()
def fake_db() -> FakeSupabaseClient:
    return FakeSupabaseClient()


@pytest.fixture()
def client(fake_db: FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_and_list_metadata_field_definitions(client: TestClient) -> None:
    resp = client.post(
        "/api/metadata-fields",
        json={"name": "category", "description": "The business or content category."},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "category"
    assert body["description"] == "The business or content category."
    assert "id" in body

    listed = client.get("/api/metadata-fields")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["name"] == "category"


def test_create_rejects_blank_name_or_description(client: TestClient) -> None:
    resp = client.post("/api/metadata-fields", json={"name": "  ", "description": "something"})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_request"

    resp2 = client.post("/api/metadata-fields", json={"name": "category", "description": "  "})
    assert resp2.status_code == 400


def test_list_only_returns_current_users_field_definitions(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    fake_db.tables["metadata_field_definition"] = [
        {
            "id": str(uuid.uuid4()),
            "user_id": str(OTHER_USER_ID),
            "name": "not mine",
            "description": "belongs to someone else",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    resp = client.get("/api/metadata-fields")

    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_field_definition(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    field_id = uuid.uuid4()
    fake_db.tables["metadata_field_definition"] = [
        {
            "id": str(field_id),
            "user_id": str(USER_ID),
            "name": "category",
            "description": "desc",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    resp = client.delete(f"/api/metadata-fields/{field_id}")

    assert resp.status_code == 204
    assert fake_db.tables["metadata_field_definition"] == []


def test_delete_not_found_returns_404(client: TestClient) -> None:
    resp = client.delete(f"/api/metadata-fields/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_delete_owned_by_another_user_returns_404(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    field_id = uuid.uuid4()
    fake_db.tables["metadata_field_definition"] = [
        {
            "id": str(field_id),
            "user_id": str(OTHER_USER_ID),
            "name": "not mine",
            "description": "desc",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    resp = client.delete(f"/api/metadata-fields/{field_id}")

    assert resp.status_code == 404
    # not actually deleted
    assert len(fake_db.tables["metadata_field_definition"]) == 1
