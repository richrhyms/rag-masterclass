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


def test_upload_docx_is_accepted_without_eager_utf8_validation(client: TestClient) -> None:
    # Binary content that is NOT valid UTF-8 -- must not be rejected at
    # upload time (docx is a binary zip format). Real extraction happens
    # in the background pipeline (python-docx), covered separately in
    # test_ingestion_pipeline.py.
    resp = client.post(
        "/api/documents",
        files={
            "file": (
                "doc.docx",
                b"PK\xff\xfe not real docx bytes",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_upload_html_is_accepted(client: TestClient) -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("page.html", b"<html><body><p>hello</p></body></html>", "text/html")},
    )
    assert resp.status_code == 202
    assert resp.json()["content_type"] == "text/html"


def test_upload_rejects_unsupported_extension_still_rejects_other_binaries(client: TestClient) -> None:
    # docx/html acceptance above must not have widened the extension
    # allowlist beyond what's actually supported.
    resp = client.post(
        "/api/documents",
        files={"file": ("archive.zip", b"PK\x03\x04 fake zip", "application/zip")},
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


# --- Module 3: content-hash dedup + incremental re-ingest ---


def test_upload_rejects_exact_duplicate_content(client: TestClient, fake_client: FakeSupabaseClient) -> None:
    content = b"identical content, uploaded twice"
    first = client.post("/api/documents", files={"file": ("notes.txt", content, "text/plain")})
    assert first.status_code == 202

    second = client.post("/api/documents", files={"file": ("different-name.txt", content, "text/plain")})

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "duplicate_content"
    assert "notes.txt" in second.json()["detail"]["error"]
    # no second document row or pipeline kickoff -- rejected before any processing
    assert len(fake_client.tables["document"]) == 1
    assert len(client.pipeline_calls) == 1  # type: ignore[attr-defined]


def test_upload_allows_same_content_reuploaded_after_failure(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    content = b"content that previously failed to ingest"
    first = client.post("/api/documents", files={"file": ("notes.txt", content, "text/plain")})
    assert first.status_code == 202
    fake_client.tables["document"][0]["status"] = "failed"

    second = client.post("/api/documents", files={"file": ("notes.txt", content, "text/plain")})

    assert second.status_code == 202
    assert len(fake_client.tables["document"]) == 2


def test_upload_same_filename_different_content_supersedes_old_version(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    old_document_id = uuid.uuid4()
    old_storage_path = f"documents/{USER_ID}/{old_document_id}/report.txt"
    fake_client.storage.buckets["documents"] = {f"{USER_ID}/{old_document_id}/report.txt": b"old data"}
    fake_client.table("document").insert(
        {
            "id": str(old_document_id),
            "user_id": str(USER_ID),
            "filename": "report.txt",
            "storage_path": old_storage_path,
            "content_type": "text/plain",
            "byte_size": 8,
            "status": "completed",
            "content_hash": "old-hash",
        }
    ).execute()

    resp = client.post(
        "/api/documents",
        files={"file": ("report.txt", b"revised report content", "text/plain")},
    )

    assert resp.status_code == 202
    # old version's row and storage object are gone; only the new one remains
    remaining = fake_client.tables["document"]
    assert len(remaining) == 1
    assert remaining[0]["id"] != str(old_document_id)
    assert remaining[0]["filename"] == "report.txt"
    assert f"{USER_ID}/{old_document_id}/report.txt" not in fake_client.storage.buckets["documents"]


def test_upload_does_not_supersede_a_failed_same_filename_document(
    client: TestClient, fake_client: FakeSupabaseClient
) -> None:
    old_document_id = uuid.uuid4()
    fake_client.table("document").insert(
        {
            "id": str(old_document_id),
            "user_id": str(USER_ID),
            "filename": "report.txt",
            "storage_path": f"documents/{USER_ID}/{old_document_id}/report.txt",
            "content_type": "text/plain",
            "byte_size": 8,
            "status": "failed",
            "content_hash": "old-hash",
        }
    ).execute()

    resp = client.post(
        "/api/documents",
        files={"file": ("report.txt", b"fresh attempt at the same filename", "text/plain")},
    )

    assert resp.status_code == 202
    # the failed row is left alone (not superseded) -- both rows now exist
    remaining = fake_client.tables["document"]
    assert len(remaining) == 2
    assert any(row["id"] == str(old_document_id) for row in remaining)


def test_upload_supersede_leaves_old_document_intact_if_new_upload_fails(
    client: TestClient, fake_client: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: an earlier version of the supersede logic deleted
    the old document BEFORE the new upload was confirmed, so a Storage
    failure right after permanently lost the old document with nothing to
    replace it. The delete must happen last, only once the new document is
    fully committed."""
    old_document_id = uuid.uuid4()
    old_storage_path = f"documents/{USER_ID}/{old_document_id}/report.txt"
    fake_client.storage.buckets["documents"] = {f"{USER_ID}/{old_document_id}/report.txt": b"old data"}
    fake_client.table("document").insert(
        {
            "id": str(old_document_id),
            "user_id": str(USER_ID),
            "filename": "report.txt",
            "storage_path": old_storage_path,
            "content_type": "text/plain",
            "byte_size": 8,
            "status": "completed",
            "content_hash": "old-hash",
        }
    ).execute()

    from app.routers import documents as documents_module

    def _boom_upload(*_args, **_kwargs):
        raise RuntimeError("transient storage failure")

    monkeypatch.setattr(documents_module.storage, "upload_document", _boom_upload)

    resp = client.post(
        "/api/documents",
        files={"file": ("report.txt", b"revised report content", "text/plain")},
    )

    assert resp.status_code == 500
    # the old document must still be fully intact -- both the row and its
    # storage object -- since the new upload never succeeded
    remaining = fake_client.tables["document"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == str(old_document_id)
    assert f"{USER_ID}/{old_document_id}/report.txt" in fake_client.storage.buckets["documents"]


def test_upload_supersede_leaves_old_document_intact_if_insert_fails(
    client: TestClient, fake_client: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    old_document_id = uuid.uuid4()
    old_storage_path = f"documents/{USER_ID}/{old_document_id}/report.txt"
    fake_client.storage.buckets["documents"] = {f"{USER_ID}/{old_document_id}/report.txt": b"old data"}
    fake_client.table("document").insert(
        {
            "id": str(old_document_id),
            "user_id": str(USER_ID),
            "filename": "report.txt",
            "storage_path": old_storage_path,
            "content_type": "text/plain",
            "byte_size": 8,
            "status": "completed",
            "content_hash": "old-hash",
        }
    ).execute()

    # The fixture setup above already completed its own "document" table
    # insert before the request is made, so the *next* insert on that
    # table is unambiguously the new document's -- fail exactly that one.
    original_table = fake_client.table

    def _boom_on_next_document_insert(name, *args, **kwargs):
        query = original_table(name, *args, **kwargs)
        if name == "document":
            def _insert(_payload):
                raise RuntimeError("transient db failure")

            query.insert = _insert
        return query

    monkeypatch.setattr(fake_client, "table", _boom_on_next_document_insert)

    resp = client.post(
        "/api/documents",
        files={"file": ("report.txt", b"revised report content", "text/plain")},
    )

    assert resp.status_code == 500
    remaining = fake_client.tables["document"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == str(old_document_id)
    assert f"{USER_ID}/{old_document_id}/report.txt" in fake_client.storage.buckets["documents"]


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
