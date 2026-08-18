"""
Chunk -> embed -> persist ingestion pipeline + Realtime status transitions.

design.md "Module Boundaries": backend/app/services/ingestion.py, owner:
backend-2 (G-5b). Implements the write side of DD-2 (backend-2 writes
`chunk` rows conforming exactly to the frozen `chunk` schema; it never calls
retrieval) and the "Realtime status contract" section (queued -> processing
-> completed|failed, each transition a separate UPDATE with `status`,
`updated_at`, and on terminal state `chunk_count`/`error`).

Ingestion-specific Pydantic models live here (not in the frozen
`app/models.py`), per the mailbox's file-ownership note.

Client usage:
- The initial `document` row insert (queued) happens in the request path
  using the caller's request-scoped (RLS) client -- see
  `app/routers/documents.py`.
- Everything in this module (status transitions AND chunk inserts) uses the
  **service-role** client. Rationale: the pipeline is kicked off via
  `BackgroundTasks` and is decoupled from the request/response cycle -- it
  is not appropriate to depend on the caller's JWT still being valid for an
  unbounded-duration background job. design.md mandates the service-role
  client for status writes explicitly; this module additionally uses it for
  chunk inserts for the same background-task-lifetime reason. Every write
  is scoped by an explicit `.eq("user_id", ...)` filter (in addition to
  `.eq("id", ...)`) so an update can never touch another user's row even
  though the service role bypasses RLS -- this is the "Status writes set
  `user_id` explicitly" requirement from the mailbox.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable
from uuid import UUID

from openai import OpenAI
from pydantic import BaseModel
from supabase import Client

from app.config import Settings
from app.models import DocumentOut

logger = logging.getLogger("rag_masterclass.ingestion")


# --- Ingestion-specific Pydantic models (additive; NOT in app/models.py) ---


class DocumentUploadOut(BaseModel):
    """Response shape for POST /api/documents (202 Accepted). Distinct from
    the frozen `DocumentOut` (which is the GET /api/documents list shape) --
    it carries `content_type` and omits `chunk_count`/`error`/`updated_at`,
    per design.md's API Contracts section."""

    id: UUID
    filename: str
    status: str
    content_type: str
    byte_size: int
    created_at: datetime


class DocumentsListResponse(BaseModel):
    documents: list[DocumentOut]


class IngestionError(Exception):
    """Raised for pipeline failures that should terminate the document in
    `status='failed'` with a human-readable `error`."""


# --- Chunking (FR-ING-2, FR-BE-6) ---


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Character-based sliding-window chunking (CHUNK_SIZE/CHUNK_OVERLAP,
    defaults 1000/150 chars per design.md's env-var contract). No NLP/token
    libraries -- plain character slicing, consistent with FR-BE-3/NFR-5 (no
    LLM/orchestration frameworks)."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        chunk_overlap = 0

    stripped = text.strip()
    if not stripped:
        return []

    step = chunk_size - chunk_overlap
    length = len(stripped)
    chunks: list[str] = []
    start = 0
    while start < length:
        end = min(start + chunk_size, length)
        piece = stripped[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == length:
            break
        start += step
    return chunks


# --- Embedding (raw OpenAI-compatible SDK only -- FR-BE-3/NFR-5) ---


def _default_embed(chunks: list[str], settings: Settings) -> list[list[float]]:
    """Embeds `chunks` via the configured OpenAI-compatible embeddings
    endpoint. Gated behind EMBEDDING_BASE_URL/EMBEDDING_API_KEY so this
    never fires (and never requires real credentials) unless the operator
    has configured a Module 2 provider -- keeps unit tests provider-key-free
    per the mailbox's runtime note."""
    if not settings.EMBEDDING_BASE_URL or not settings.EMBEDDING_API_KEY:
        raise IngestionError(
            "Embeddings provider not configured: set EMBEDDING_BASE_URL, "
            "EMBEDDING_API_KEY, and EMBEDDING_MODEL to enable ingestion."
        )

    client = OpenAI(base_url=settings.EMBEDDING_BASE_URL, api_key=settings.EMBEDDING_API_KEY)
    response = client.embeddings.create(model=settings.EMBEDDING_MODEL, input=chunks)
    # response.data is returned in the same order as the input list.
    return [item.embedding for item in response.data]


EmbedFn = Callable[[list[str], Settings], list[list[float]]]


# --- Status transitions (Realtime status contract) ---


def _update_status(
    service_client: Client,
    *,
    document_id: UUID,
    user_id: UUID,
    document_status: str,
    chunk_count: int | None = None,
    error: str | None = None,
) -> None:
    """Writes a single status transition as its own UPDATE (design.md:
    "Backend-2 MUST write each transition ... as separate UPDATEs so each
    transition is observable live" via Supabase Realtime on `public.document`).
    Always scopes the WHERE clause with both `id` and `user_id` explicitly,
    even though the service-role client bypasses RLS."""
    payload: dict[str, object] = {
        "status": document_status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if chunk_count is not None:
        payload["chunk_count"] = chunk_count
    if document_status == "failed":
        payload["error"] = error or "Unknown ingestion error"

    (
        service_client.table("document")
        .update(payload)
        .eq("id", str(document_id))
        .eq("user_id", str(user_id))
        .execute()
    )


# --- The pipeline (FR-ING-2, FR-BE-6) ---


def run_ingestion_pipeline(
    *,
    service_client: Client,
    document_id: UUID,
    user_id: UUID,
    text: str,
    settings: Settings,
    embed_fn: EmbedFn = _default_embed,
) -> None:
    """Runs chunk -> embed -> persist for one document, writing each status
    transition separately. Intended to be invoked as a FastAPI
    `BackgroundTasks` callback from `POST /api/documents` after the
    `document` row is inserted with `status='queued'`.

    `embed_fn` is injectable so callers (tests) can avoid a real embeddings
    provider round-trip; production code should rely on the default.

    On any failure, writes the terminal `status='failed'` with `error`
    populated -- `failed` is always terminal, never a stuck non-terminal
    state (AC-ING-3b).
    """
    try:
        _update_status(
            service_client,
            document_id=document_id,
            user_id=user_id,
            document_status="processing",
        )

        chunks = chunk_text(text, settings.CHUNK_SIZE, settings.CHUNK_OVERLAP)
        if not chunks:
            _update_status(
                service_client,
                document_id=document_id,
                user_id=user_id,
                document_status="failed",
                error="No extractable text content in the uploaded file.",
            )
            return

        embeddings = embed_fn(chunks, settings)
        if len(embeddings) != len(chunks):
            raise IngestionError(
                f"Embeddings provider returned {len(embeddings)} vectors for "
                f"{len(chunks)} chunks."
            )
        for vector in embeddings:
            if len(vector) != settings.EMBEDDING_DIM:
                raise IngestionError(
                    f"Embedding dimension {len(vector)} does not match "
                    f"EMBEDDING_DIM={settings.EMBEDDING_DIM}."
                )

        rows = [
            {
                "document_id": str(document_id),
                "user_id": str(user_id),
                "chunk_index": index,
                "content": chunk,
                "embedding": vector,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, embeddings))
        ]
        service_client.table("chunk").insert(rows).execute()

        _update_status(
            service_client,
            document_id=document_id,
            user_id=user_id,
            document_status="completed",
            chunk_count=len(chunks),
        )
    except Exception as exc:
        logger.exception("Ingestion pipeline failed for document %s", document_id)
        try:
            _update_status(
                service_client,
                document_id=document_id,
                user_id=user_id,
                document_status="failed",
                error=str(exc)[:2000],
            )
        except Exception:  # pragma: no cover - last-resort logging only
            logger.exception(
                "Failed to write terminal 'failed' status for document %s", document_id
            )
