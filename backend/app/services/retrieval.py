"""
Chat-time pgvector retrieval read-path (design.md "DD-2 -- Retrieval
interface (shared chat-read <-> ingestion-write contract)", owner: backend-1,
G-5a). This is the single seam between backend-1 (chat read-path, here) and
backend-2 (ingestion write-path, `services/ingestion.py`) -- both build
against the frozen signature below with no cross-coordination.

`retrieve_chunks` embeds the query, calls the `match_chunks` Postgres RPC
(defined in the G-4 migration, `SECURITY INVOKER` so RLS still applies), and
returns `RetrievedChunk` rows (imported read-only from the frozen
`app/models.py`). Per DD-2:
  - User scoping is enforced twice: RLS (`user_id = auth.uid()`, via the
    request-scoped `db` client) AND an explicit `match_user` predicate in the
    RPC call (NFR-13 defense-in-depth, AC-BE-7b).
  - An empty result is valid, not an error (AC-CHAT-4b).
  - Any failure (embedding call or RPC call) is logged and treated as `[]` so
    retrieval can never fail the chat request.
"""
from __future__ import annotations

import logging
from uuid import UUID

from supabase import Client

from app.config import Settings, get_settings
from app.models import RetrievedChunk
from app.services.llm import embed_text

logger = logging.getLogger("rag_masterclass.retrieval")


async def retrieve_chunks(
    db: Client,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Frozen signature (design.md DD-2). Returns the top `top_k` chunks owned
    by `user_id` most similar (cosine) to `query`, or `[]` on any failure or
    when nothing relevant is found."""
    settings: Settings = get_settings()

    try:
        query_embedding = await embed_text(query, settings)
    except Exception:
        logger.exception("Embedding failed during chat-time retrieval; treating as a retrieval miss")
        return []

    try:
        response = db.rpc(
            "match_chunks",
            {
                "query_embedding": query_embedding,
                "match_user": str(user_id),
                "match_count": top_k,
            },
        ).execute()
    except Exception:
        logger.exception("match_chunks RPC failed during chat-time retrieval; treating as a retrieval miss")
        return []

    rows = response.data or []
    return [RetrievedChunk(**row) for row in rows]
