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


def _run_pipeline(*, text: str, **kwargs):
    """Test helper: these tests predate multi-format support and are about
    chunk/embed/persist behavior, not extraction -- they pass plain `text`
    (encoded as .txt) rather than exercising the PDF-specific
    `_default_extract` path, which is covered separately below."""
    return run_ingestion_pipeline(
        raw=text.encode("utf-8"),
        content_type="text/plain",
        filename="test.txt",
        **kwargs,
    )


def test_pipeline_happy_path_writes_chunks_and_completes() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=10, CHUNK_OVERLAP=0, EMBEDDING_DIM=3)

    # Seed the document row as the router would (status='queued').
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    _run_pipeline(
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
        _run_pipeline(
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

    _run_pipeline(
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

    _run_pipeline(
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

    _run_pipeline(
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

    _run_pipeline(
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
    _run_pipeline(
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


# --- _default_extract (PDF support via pypdf, PRD Module 5 pulled forward
#     for PDF only -- docling was tried first but has no PyTorch wheel for
#     Intel Mac + Python 3.13, a hard platform incompatibility) ---
#
# Unlike docling, pypdf is lightweight (no ML/layout models), so its real
# PDF path IS exercised directly below with an actual generated PDF, not
# just via injected fakes. `run_ingestion_pipeline` still takes an
# injectable `extract_fn` (matching the existing `embed_fn` pattern) so
# pipeline-level tests don't need a real file on disk -- see the two tests
# after the real-PDF one for how callers exercise that seam instead.


def test_default_extract_decodes_plain_text_directly() -> None:
    assert ingestion._default_extract(b"hello world", "text/plain", "notes.txt") == "hello world"
    assert ingestion._default_extract(b"# Title", "text/markdown", "readme.md") == "# Title"


def test_default_extract_raises_ingestion_error_on_invalid_utf8() -> None:
    with pytest.raises(IngestionError):
        ingestion._default_extract(b"\xff\xfe", "text/plain", "notes.txt")


def _make_pdf_bytes(text: str) -> bytes:
    """Builds a real, valid single-page PDF containing `text`, for testing
    `_default_extract`'s pypdf path against actual PDF bytes rather than a
    hand-rolled/injected fake."""
    import io

    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(72, 720, text)
    c.save()
    return buffer.getvalue()


def test_default_extract_reads_real_pdf() -> None:
    pdf_bytes = _make_pdf_bytes("The secret code is ORCHID-7734.")
    extracted = ingestion._default_extract(pdf_bytes, "application/pdf", "report.pdf")
    assert "ORCHID-7734" in extracted


def test_default_extract_raises_ingestion_error_on_corrupt_pdf() -> None:
    with pytest.raises(IngestionError):
        ingestion._default_extract(b"not a real pdf at all", "application/pdf", "report.pdf")


def test_default_extract_raises_ingestion_error_on_unsupported_content_type() -> None:
    with pytest.raises(IngestionError):
        ingestion._default_extract(b"fake docx bytes", "application/vnd.openxmlformats", "doc.docx")


_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_docx_bytes(*paragraphs: str) -> bytes:
    """Builds a real, valid .docx containing `paragraphs`, for testing
    `_default_extract`'s python-docx path against actual DOCX bytes rather
    than a hand-rolled/injected fake."""
    import io

    from docx import Document as DocxDocument

    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_default_extract_reads_real_docx() -> None:
    docx_bytes = _make_docx_bytes("The secret code is ORCHID-7734.", "Second paragraph.")
    extracted = ingestion._default_extract(docx_bytes, _DOCX_CONTENT_TYPE, "report.docx")
    assert "ORCHID-7734" in extracted
    assert "Second paragraph." in extracted


def test_default_extract_skips_empty_docx_paragraphs() -> None:
    docx_bytes = _make_docx_bytes("First.", "", "   ", "Second.")
    extracted = ingestion._default_extract(docx_bytes, _DOCX_CONTENT_TYPE, "report.docx")
    assert extracted == "First.\n\nSecond."


def test_default_extract_raises_ingestion_error_on_corrupt_docx() -> None:
    with pytest.raises(IngestionError):
        ingestion._default_extract(b"not a real docx at all", _DOCX_CONTENT_TYPE, "report.docx")


def test_default_extract_reads_real_html() -> None:
    html = b"""
    <html>
      <head><title>ignored title text</title><style>body { color: red; }</style></head>
      <body>
        <script>console.log('should not appear');</script>
        <h1>The secret code is ORCHID-7734.</h1>
        <p>Second paragraph.</p>
      </body>
    </html>
    """
    extracted = ingestion._default_extract(html, "text/html", "page.html")
    assert "ORCHID-7734" in extracted
    assert "Second paragraph." in extracted
    assert "console.log" not in extracted
    assert "color: red" not in extracted


def test_default_extract_html_falls_back_to_latin1_on_non_utf8() -> None:
    # a byte sequence that is invalid UTF-8 but valid latin-1
    html = b"<html><body><p>caf\xe9</p></body></html>"
    extracted = ingestion._default_extract(html, "text/html", "page.html")
    assert "café" in extracted


def test_pipeline_uses_injected_extract_fn_for_non_text_formats() -> None:
    """Confirms the pipeline actually calls `extract_fn` with the raw bytes/
    content_type/filename it was given, rather than assuming plain text --
    this is the seam that lets a caller swap in a fake without a real file
    on disk for pipeline-level (as opposed to extraction-level) tests."""
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(CHUNK_SIZE=1000, CHUNK_OVERLAP=150, EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    captured: dict = {}

    def _fake_extract(raw: bytes, content_type: str, filename: str) -> str:
        captured.update(raw=raw, content_type=content_type, filename=filename)
        return "extracted PDF text content"

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        raw=b"%PDF-1.4 fake pdf bytes",
        content_type="application/pdf",
        filename="report.pdf",
        settings=settings,
        extract_fn=_fake_extract,
        embed_fn=_fake_embed(3),
    )

    assert captured == {
        "raw": b"%PDF-1.4 fake pdf bytes",
        "content_type": "application/pdf",
        "filename": "report.pdf",
    }
    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "completed"


def test_pipeline_extract_fn_failure_marks_failed_terminal() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    def _boom_extract(_raw: bytes, _content_type: str, _filename: str) -> str:
        raise IngestionError("Failed to extract text from 'corrupt.pdf': bad xref table")

    run_ingestion_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        raw=b"not a real pdf",
        content_type="application/pdf",
        filename="corrupt.pdf",
        settings=settings,
        extract_fn=_boom_extract,
        embed_fn=_fake_embed(3),
    )

    [doc_row] = client.tables["document"]
    assert doc_row["status"] == "failed"
    assert "bad xref table" in doc_row["error"]
    assert client.tables.get("chunk", []) == []


# --- Metadata extraction (Module 4, PRD) ---


def test_pipeline_skips_metadata_extraction_when_no_field_definitions() -> None:
    """A user with no metadata_field_definition rows configured pays zero
    extra LLM cost -- extract_metadata_fn must not even be called."""
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()

    calls: list[tuple] = []

    def _spy_extract_metadata(text, field_definitions, settings):
        calls.append((text, field_definitions))
        return {}

    _run_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="some document text",
        settings=settings,
        embed_fn=_fake_embed(3),
        extract_metadata_fn=_spy_extract_metadata,
    )

    assert calls == []
    [doc_row] = client.tables["document"]
    assert doc_row["metadata"] == {}


def test_pipeline_calls_metadata_extraction_when_field_definitions_exist() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()
    client.table("metadata_field_definition").insert(
        {"user_id": str(user_id), "name": "category", "description": "The document category."}
    ).execute()

    calls: list[tuple] = []

    def _spy_extract_metadata(text, field_definitions, settings):
        calls.append((text, field_definitions))
        return {"category": "Policy Report"}

    _run_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="a policy document about solar incentives",
        settings=settings,
        embed_fn=_fake_embed(3),
        extract_metadata_fn=_spy_extract_metadata,
    )

    [(text, field_definitions)] = calls
    assert text == "a policy document about solar incentives"
    # the fake's .select() doesn't project columns (unlike real Postgrest),
    # so extra fields may be present -- only name/description matter to
    # _default_extract_metadata's contract
    assert len(field_definitions) == 1
    assert field_definitions[0]["name"] == "category"
    assert field_definitions[0]["description"] == "The document category."

    [doc_row] = client.tables["document"]
    assert doc_row["metadata"] == {"category": "Policy Report"}


def test_pipeline_only_fetches_field_definitions_for_the_ingesting_user() -> None:
    client = FakeSupabaseClient()
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    settings = _settings(EMBEDDING_DIM=3)
    client.table("document").insert(
        {"id": str(document_id), "user_id": str(user_id), "status": "queued"}
    ).execute()
    client.table("metadata_field_definition").insert(
        {"user_id": str(other_user_id), "name": "not mine", "description": "belongs to someone else"}
    ).execute()

    calls: list[tuple] = []

    def _spy_extract_metadata(text, field_definitions, settings):
        calls.append(field_definitions)
        return {}

    _run_pipeline(
        service_client=client,
        document_id=document_id,
        user_id=user_id,
        text="some text",
        settings=settings,
        embed_fn=_fake_embed(3),
        extract_metadata_fn=_spy_extract_metadata,
    )

    assert calls == []  # never called -- no field definitions for THIS user


class _FakeMetadataMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeMetadataChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMetadataMessage(content)


class _FakeMetadataResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeMetadataChoice(content)]


class _FakeMetadataCompletions:
    def __init__(self, content: str | None) -> None:
        self._content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMetadataResponse(self._content)


class _FakeMetadataClient:
    def __init__(self, content: str | None) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeMetadataCompletions(content)


_CATEGORY_FIELD = {"name": "category", "description": "The document category."}


def test_default_extract_metadata_parses_well_formed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeMetadataClient('{"category": "Policy Report"}')
    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: fake_client)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("some document text", [_CATEGORY_FIELD], settings)

    assert result == {"category": "Policy Report"}
    call = fake_client.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert "some document text" in call["messages"][1]["content"]


def test_default_extract_metadata_strips_markdown_code_fence(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeMetadataClient('```json\n{"category": "Policy Report"}\n```')
    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: fake_client)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": "Policy Report"}


def test_default_extract_metadata_maps_missing_field_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeMetadataClient("{}")  # model didn't mention "category" at all
    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: fake_client)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": None}


def test_default_extract_metadata_falls_back_to_empty_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeMetadataClient("not valid json at all")
    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: fake_client)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": None}


def test_default_extract_metadata_falls_back_to_empty_on_non_dict_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeMetadataClient("[1, 2, 3]")
    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: fake_client)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": None}


def test_default_extract_metadata_falls_back_to_empty_on_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("provider network error")

    class _ExplodingClient:
        def __init__(self) -> None:
            self.chat = type("_Chat", (), {})()
            self.chat.completions = _ExplodingCompletions()

    monkeypatch.setattr(ingestion, "OpenAI", lambda **_kwargs: _ExplodingClient())
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": None}


def test_default_extract_metadata_skips_llm_call_when_unconfigured() -> None:
    settings = _settings(LLM_BASE_URL=None, LLM_API_KEY=None, LLM_MODEL=None)

    result = ingestion._default_extract_metadata("text", [_CATEGORY_FIELD], settings)

    assert result == {"category": None}


def test_default_extract_metadata_returns_empty_immediately_for_no_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs):
        raise AssertionError("OpenAI client should never be constructed with no field definitions")

    monkeypatch.setattr(ingestion, "OpenAI", _boom)
    settings = _settings(LLM_BASE_URL="https://api.example.com", LLM_API_KEY="key", LLM_MODEL="model")

    result = ingestion._default_extract_metadata("text", [], settings)

    assert result == {}
