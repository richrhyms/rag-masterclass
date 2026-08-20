import uuid

import pytest

from app.config import Settings
from app.services import ingestion
from app.services.ingestion import IngestionError, chunk_text, run_ingestion_pipeline
from tests.ingestion_fakes import FakeSupabaseClient


def _settings(**overrides) -> Settings:
    # SUPABASE_* required fields are already populated from env by
    # tests/conftest.py; overrides layer on top for the fields this
    # module's tests actually vary.
    return Settings(**overrides)


# --- chunk_text (FR-ING-2, FR-BE-6) ---


def test_chunk_text_splits_on_size_with_overlap() -> None:
    text = "a" * 25
    chunks = chunk_text(text, chunk_size=10, chunk_overlap=2)
    assert chunks == ["a" * 10, "a" * 10, "a" * 9]


def test_chunk_text_empty_or_whitespace_returns_no_chunks() -> None:
    assert chunk_text("", chunk_size=1000, chunk_overlap=150) == []
    assert chunk_text("   \n\t  ", chunk_size=1000, chunk_overlap=150) == []


def test_chunk_text_single_chunk_when_shorter_than_chunk_size() -> None:
    assert chunk_text("hello world", chunk_size=1000, chunk_overlap=150) == ["hello world"]


def test_chunk_text_invalid_overlap_falls_back_to_zero() -> None:
    # overlap >= chunk_size is nonsensical; the function defensively resets
    # it to 0 rather than looping forever / producing garbage.
    chunks = chunk_text("a" * 20, chunk_size=5, chunk_overlap=5)
    assert chunks == ["a" * 5, "a" * 5, "a" * 5, "a" * 5]


def test_chunk_text_rejects_non_positive_chunk_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("hello", chunk_size=0, chunk_overlap=0)


# --- run_ingestion_pipeline: status transitions + persistence ---


def _fake_embed(dim: int):
    def _embed(chunks: list[str], _settings: Settings) -> list[list[float]]:
        return [[0.1] * dim for _ in chunks]

    return _embed


def test_pipeline_happy_path_writes_chunks_and_completes() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=10, CHUNK_OVERLAP=0, EMBEDDING_DIM=3)

    # Seed the document row as the router would (status='queued').
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="a" * 25,
        settings=settings,
        embed_fn=_fake_embed(3),
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "completed"
    assert doc_row["chunk_count"] == 3
    assert "updated_at" in doc_row

    chunk_rows = client.tables["chunk"]
    assert len(chunk_rows) == 3
    for index, row in enumerate(chunk_rows):
        assert row["document_id"] == str(document_id)
        assert row["user_id"] == str(user_id)
        assert row["chunk_index"] == index
        assert row["embedding"] == [0.1, 0.1, 0.1]
        assert isinstance(row["content"], str) and row["content"]


def test_pipeline_writes_processing_then_completed_as_separate_updates() -> None:
    """design.md's Realtime status contract: each transition is a distinct
    UPDATE (observable independently via Supabase Realtime), not a single
    combined write."""
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=1000, CHUNK_OVERLAP=150, EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    observed_statuses: list[str] = []
    real = ingestion._update_status

    def _spy(service_client, **kwargs):
        observed_statuses.append(kwargs["document_status"])
        return real(service_client, **kwargs)

    ingestion._update_status = _spy
    try:
        run_ingestion_pipeline(
            service_client=client,
            document_id=document_id,
            user_id=user_id,
            text="some short document text",
            settings=settings,
            embed_fn=_fake_embed(3),
        )
    finally:
        ingestion._update_status = real

    assert observed_statuses == ["processing", "completed"]


def test_pipeline_empty_extractable_text_marks_failed_terminal() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="   \n  ",
        settings=settings,
        embed_fn=_fake_embed(3),
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "failed"
    assert doc_row["error"]
    assert client.tables.get("chunk", []) == []


def test_pipeline_embed_fn_exception_marks_failed_terminal_with_error() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    def _boom(_chunks: list[str], _settings: Settings) -> list[list[float]]:
        raise RuntimeError("embeddings provider unreachable")

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="some real content here",
        settings=settings,
        embed_fn=_boom,
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "failed"
    assert "embeddings provider unreachable" in doc_row["error"]


def test_pipeline_embed_count_mismatch_marks_failed() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=10, CHUNK_OVERLAP=0, EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    def _too_few(chunks: list[str], _settings: Settings) -> list[list[float]]:
        return [[0.1, 0.1, 0.1]]  # fewer vectors than chunks

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="a" * 25,
        settings=settings,
        embed_fn=_too_few,
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "failed"
    assert "Embeddings provider returned" in doc_row["error"]


def test_pipeline_embed_dimension_mismatch_marks_failed() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=1000, CHUNK_OVERLAP=150, EMBEDDING_DIM=1536)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="short text",
        settings=settings,
        embed_fn=_fake_embed(3),  # wrong dimension vs EMBEDDING_DIM=1536
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "failed"
    assert "does not match" in doc_row["error"]


def test_pipeline_status_writes_scope_by_user_id() -> None:
    """A status write must never touch another user's row, even though the
    service-role client bypasses RLS (mailbox: 'Status writes set user_id
    explicitly')."""
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)

    client.table("document").insert(
        {"id": str(document_id), "user_id": str(owner_id), "status": "queued"}
    ).execute()

    # Attempt to run the pipeline "as" a different user_id than the row's
    # owner -- the .eq("user_id", ...) scoping means the update matches
    # nothing, so the row must remain untouched (still 'queued'), not get
    # silently completed under someone else's identity.
    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=other_user_id,
        text="a" * 25,
        settings=settings,
        embed_fn=_fake_embed(3),
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "queued"


# --- _default_embed gating (no real provider keys required for tests) ---


def test_default_embed_raises_ingestion_error_when_unconfigured() -> None:
    settings = _settings(EMBEDDING_BASE_URL=None, EMBEDDING_API_KEY=None)
    with pytest.raises(IngestionError):
        ingestion._default_embed(["chunk one"], settings)
