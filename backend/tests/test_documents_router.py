import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.deps import CurrentUser, get_current_user, get_db, get_service_client
from app.main import app
from app.services import ingestion
from tests.ingestion_fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


def _override_settings(**overrides) -> Settings:
    return Settings(**overrides)


@pytest.fixture()
def fake_client() -> FakeSupabaseClient:
    return FakeSupabaseClient()


@pytest.fixture()
def client(fake_client: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Route the router's ingestion kickoff through a recording stub instead
    # of the real pipeline -- the pipeline itself (chunk/embed/persist/status
    # transitions) is exercised directly in test_ingestion_pipeline.py.
    calls: list[dict] = []

    def _fake_run_ingestion_pipeline(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(ingestion, "run_ingestion_pipeline", _fake_run_ingestion_pipeline)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=USER_ID, email="user@example.com", claims={}
    )
    app.dependency_overrides[get_db] = lambda: fake_client
    app.dependency_overrides[get_service_client] = lambda: fake_client
    app.dependency_overrides[get_settings] = lambda: _override_settings()

    test_client = TestClient(app)
    test_client.pipeline_calls = calls  # type: ignore[attr-defined]
    yield test_client

    app.dependency_overrides.clear()


# --- POST /api/documents ---


def test_upload_txt_returns_202_and_queues_document(client: TestClient, fake_client) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("notes.txt", b"hello world, this is a test document.", "text/plain")},
    )

    assert resp.status_code == 202
    body = resp.json()
    assert body["filename"] == "notes.txt"
    assert body["status"] == "queued"
    assert body["content_type"] == "text/plain"
    assert body["byte_size"] == len(b"hello world, this is a test document.")
    assert "id" in body and "created_at" in body

    [doc_row] = fake_client.tables["document"]
    assert doc_row["status"] == "queued"
    assert doc_row["user_id"] == str(USER_ID)
    assert doc_row["storage_path"].startswith("documents/")

    [call] = client.pipeline_calls  # type: ignore[attr-defined]
    assert call["document_id"] == uuid.UUID(body["id"])
    assert call["user_id"] == USER_ID
    assert call["text"] == "hello world, this is a test document."


def test_upload_md_is_accepted(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("readme.md", b"# Title\n\nbody text", "text/markdown")},
    )
    assert resp.status_code == 202
    assert resp.json()["content_type"] == "text/markdown"


def test_upload_rejects_unsupported_extension(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("scan.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "unsupported_file_type"


def test_upload_rejects_oversized_file(fake_client: FakeSupabaseClient, monkeypatch) -> None:
    monkeypatch.setattr(ingestion, "run_ingestion_pipeline", lambda **_kwargs: None)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=USER_ID, email=None, claims={}
    )
    app.dependency_overrides[get_db] = lambda: fake_client
    app.dependency_overrides[get_service_client] = lambda: fake_client
    app.dependency_overrides[get_settings] = lambda: _override_settings(MAX_UPLOAD_BYTES=10)

    test_client = TestClient(app)
    try:
        resp = test_client.post(
            "/api/documents",
            files={"file": ("big.txt", b"x" * 100, "text/plain")},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"]["code"] == "file_too_large"
        assert fake_client.tables.get("document", []) == []
    finally:
        app.dependency_overrides.clear()


def test_upload_rejects_empty_file(client: TestClient) -> None:
    resp = client.post("/api/documents", files={"file": ("empty.txt", b"", "text/plain")})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_request"


def test_upload_rejects_non_utf8_content(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("bad.txt", b"\xff\xfe\x00bad", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_request"


# --- GET /api/documents ---


def test_list_documents_scoped_to_caller_ordered_desc(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    fake_client.table("document").insert(
        {
            "id": str(uuid.uuid4()),
            "user_id": str(USER_ID),
            "filename": "older.txt",
            "status": "completed",
            "chunk_count": 2,
            "byte_size": 10,
            "error": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ).execute()
    fake_client.table("document").insert(
        {
            "id": str(uuid.uuid4()),
            "user_id": str(USER_ID),
            "filename": "newer.txt",
            "status": "queued",
            "chunk_count": 0,
            "byte_size": 20,
            "error": None,
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-01T00:00:00+00:00",
        }
    ).execute()
    fake_client.table("document").insert(
        {
            "id": str(uuid.uuid4()),
            "user_id": str(OTHER_USER_ID),
            "filename": "not-mine.txt",
            "status": "completed",
            "chunk_count": 1,
            "byte_size": 5,
            "error": None,
            "created_at": "2026-03-01T00:00:00+00:00",
            "updated_at": "2026-03-01T00:00:00+00:00",
        }
    ).execute()

    resp = client.get("/api/documents")

    assert resp.status_code == 200
    body = resp.json()
    filenames = [doc["filename"] for doc in body["documents"]]
    assert filenames == ["newer.txt", "older.txt"]


def test_list_documents_empty_for_new_user(client: TestClient) -> None:
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json() == {"documents": []}


# --- DELETE /api/documents/{document_id} ---


def test_delete_document_removes_row_and_storage_object(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    document_id = uuid.uuid4()
    storage_path = f"documents/{USER_ID}/{document_id}/f.txt"
    fake_client.storage.buckets["documents"] = {f"{USER_ID}/{document_id}/f.txt": b"data"}
    fake_client.table("document").insert(
        {
            "id": str(document_id),
            "user_id": str(USER_ID),
            "filename": "f.txt",
            "storage_path": storage_path,
            "status": "completed",
        }
    ).execute()

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 204
    assert fake_client.tables["document"] == []
    assert f"{USER_ID}/{document_id}/f.txt" not in fake_client.storage.buckets["documents"]


def test_delete_document_not_found_returns_404(client: TestClient) -> None:
    resp = client.delete(f"/api/documents/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_delete_document_owned_by_another_user_returns_404(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    document_id = uuid.uuid4()
    fake_client.table("document").insert(
        {
            "id": str(document_id),
            "user_id": str(OTHER_USER_ID),
            "filename": "f.txt",
            "storage_path": f"documents/{OTHER_USER_ID}/{document_id}/f.txt",
            "status": "completed",
        }
    ).execute()

    resp = client.delete(f"/api/documents/{document_id}")

    assert resp.status_code == 404
    # Row must remain untouched -- not owned by the caller.
    assert len(fake_client.tables["document"]) == 1
