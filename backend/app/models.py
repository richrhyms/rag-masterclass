"""
Shared Pydantic models — FROZEN after G-4 (design.md: "Module Boundaries" /
"Shared Interfaces"). Seeded by backend-1 at G-4; consumed read-only by
backend-1 (G-5a) and backend-2 (G-5b).

Do NOT edit these four models after G-4. Any new shared model must be additive
and namespaced to the owning module file (e.g. ingestion-specific models live
in app/services/ingestion.py, not here).
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    id: UUID
    document_id: UUID
    content: str
    chunk_index: int
    score: float  # cosine similarity [0,1]


class DocumentOut(BaseModel):
    id: UUID
    filename: str
    status: str  # queued|processing|completed|failed
    chunk_count: int
    byte_size: int
    error: str | None
    created_at: datetime
    updated_at: datetime
    # Document selection (post-G-4 addition): whether this document's chunks
    # are eligible for chat-time retrieval (see match_chunks in
    # supabase/migrations). Defaults true to match the DB column default, so
    # existing rows/fixtures without it deserialize unchanged.
    active: bool = True


class MessageOut(BaseModel):
    id: UUID
    role: str  # user|assistant|system
    content: str
    created_at: datetime


class ThreadOut(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime
