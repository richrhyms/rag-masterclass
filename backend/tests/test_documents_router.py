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
    assert call["raw"] == b"hello world, this is a test document."
    assert call["content_type"] == "text/plain"
    assert call["filename"] == "notes.txt"


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
        files={"file": ("image.png", b"\x89PNG fake", "image/png")},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"]["code"] == "unsupported_file_type"


def test_upload_pdf_is_accepted_without_eager_utf8_validation(client: TestClient) -> None:
    # Binary content that is NOT valid UTF-8 -- must not be rejected at
    # upload time for a binary format (unlike .txt/.md); the router only
    # eagerly UTF-8-validates plain-text content types. Real extraction
    # happens in the background pipeline (pypdf), covered separately in
    # test_ingestion_pipeline.py.
    resp = client.post(
        "/api/documents",
        files={"file": ("report.pdf", b"%PDF-1.4\xff\xfe not real pdf bytes", "application/pdf")},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["content_type"] == "application/pdf"


def test_upload_rejects_docx_and_html(client: TestClient) -> None:
    # Not supported by the pypdf-based fallback (docling would have covered
    # these but has no PyTorch wheel for this platform -- see
    # services/ingestion.py's _default_extract docstring).
    resp = client.post(
        "/api/documents",
        files={
            "file": (
                "doc.docx",
                b"fake docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 415

    resp = client.post(
        "/api/documents",
        files={"file": ("page.html", b"<html><body>hi</body></html>", "text/html")},
    )
    assert resp.status_code == 415


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


# --- PATCH /api/documents/{document_id} (document selection) ---


def test_patch_document_active_toggles_selection(client: TestClient, fake_client: FakeSupabaseClient) -> None:
    document_id = uuid.uuid4()
    fake_client.table("document").insert(
        {
            "id": str(document_id),
            "user_id": str(USER_ID),
            "filename": "f.txt",
            "status": "completed",
            "byte_size": 10,
        }
    ).execute()

    resp = client.patch(f"/api/documents/{document_id}", json={"active": False})

    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert fake_client.tables["document"][0]["active"] is False


def test_patch_document_active_not_found_returns_404(client: TestClient) -> None:
    resp = client.patch(f"/api/documents/{uuid.uuid4()}", json={"active": False})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "not_found"


def test_patch_document_active_owned_by_another_user_returns_404(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    document_id = uuid.uuid4()
    fake_client.table("document").insert(
        {
            "id": str(document_id),
            "user_id": str(OTHER_USER_ID),
            "filename": "f.txt",
            "status": "completed",
        }
    ).execute()

    resp = client.patch(f"/api/documents/{document_id}", json={"active": False})

    assert resp.status_code == 404
    # Row must remain untouched -- not owned by the caller.
    assert fake_client.tables["document"][0]["active"] is True


# --- PATCH /api/documents (bulk select-all / deselect-all) ---


def test_patch_all_documents_active_updates_every_owned_row(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    for filename in ["a.txt", "b.txt"]:
        fake_client.table("document").insert(
            {
                "id": str(uuid.uuid4()),
                "user_id": str(USER_ID),
                "filename": filename,
                "status": "completed",
                "byte_size": 10,
            }
        ).execute()
    # A document owned by someone else must be unaffected by the caller's bulk update.
    other_document_id = uuid.uuid4()
    fake_client.table("document").insert(
        {
            "id": str(other_document_id),
            "user_id": str(OTHER_USER_ID),
            "filename": "other.txt",
            "status": "completed",
            "byte_size": 10,
        }
    ).execute()

    resp = client.patch("/api/documents", json={"active": False})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["documents"]) == 2
    assert all(doc["active"] is False for doc in body["documents"])

    other_row = next(row for row in fake_client.tables["document"] if row["id"] == str(other_document_id))
    assert other_row["active"] is True


def test_patch_all_documents_active_empty_for_new_user(client: TestClient) -> None:
    resp = client.patch("/api/documents", json={"active": False})
    assert resp.status_code == 200
    assert resp.json() == {"documents": []}
