"""
Chat-time pgvector retrieval read-path (design.md "DD-2 -- Retrieval
interface (shared chat-read <-> ingestion-write contract)", owner: backend-1,
G-5a). This is the single seam between backend-1 (chat read-path, here) and
backend-2 (ingestion write-path, `services/ingestion.py`) -- both build
against the frozen signature below with no cross-coordination.

`retrieve_chunks` embeds the query, calls the `match_chunks_hybrid` Postgres
RPC (Module 6 addition, `supabase/migrations/20260912000002_hybrid_search.sql`,
`SECURITY INVOKER` so RLS still applies), reranks the fused candidate pool,
and returns `RetrievedChunk` rows (imported read-only from the frozen
`app/models.py`). Per DD-2:
  - User scoping is enforced twice: RLS (`user_id = auth.uid()`, via the
    request-scoped `db` client) AND an explicit `match_user` predicate in the
    RPC call (NFR-13 defense-in-depth, AC-BE-7b).
  - An empty result is valid, not an error (AC-CHAT-4b).
  - Any failure (embedding call or RPC call) is logged and treated as `[]` so
    retrieval can never fail the chat request.

Hybrid search + reranking (Module 6, PRD): pure cosine/vector search
(the original `match_chunks`, still defined but no longer called from here)
is weak on exact matches -- product codes, names, acronyms, numbers -- since
those don't embed distinctively. `match_chunks_hybrid` combines vector
search with Postgres full-text (keyword) search via Reciprocal Rank Fusion
(RRF) at the SQL level, over a wider candidate pool than the final
`top_k`. That fused pool is then reranked by an LLM call (`_rerank_chunks`)
that judges true semantic relevance to the query directly, rather than
RRF's rank-position heuristic, before truncating to `top_k`. Reranking is a
quality enhancement, not a correctness gate: unlike the chat guardrail's
scope classifier (which must fail loudly, see `services/chat.py`), a
reranking failure here is logged and swallowed, falling back to the
pre-rerank (RRF) order -- a suboptimal ordering is fine; failing the whole
chat turn over it is not.
"""
from __future__ import annotations

import logging
import re
from uuid import UUID

from supabase import Client

from app.config import Settings, get_settings
from app.models import RetrievedChunk
from app.services.llm import embed_text, get_chat_client

logger = logging.getLogger("rag_masterclass.retrieval")

# How much wider than `top_k` the fused candidate pool is before reranking
# narrows it back down. Wide enough that RRF's rank-based fusion has a
# meaningful pool for the LLM reranker to actually reorder within; capped
# so the reranker's prompt (and therefore latency) stays bounded regardless
# of how large `top_k` is configured.
_CANDIDATE_POOL_MULTIPLIER = 4
_MAX_CANDIDATE_POOL = 20

_RERANK_SYSTEM_PROMPT = (
    "You are a relevance reranker for a document Q&A system. You are given "
    "a user's question and a numbered list of candidate passages retrieved "
    "for it. Reorder the passage numbers from MOST to LEAST relevant to "
    "actually answering the question -- judge true semantic relevance, not "
    "just keyword overlap. Reply with ONLY a comma-separated list of the "
    "passage numbers in your reordered ranking, e.g. '3,1,4,2' -- no other "
    "text, no explanation."
)


async def _rerank_chunks(
    query: str, chunks: list[RetrievedChunk], settings: Settings, top_k: int
) -> list[RetrievedChunk]:
    """Reorders `chunks` by an LLM's judgment of relevance to `query`, then
    truncates to `top_k`. Falls back to `chunks[:top_k]` (the pre-rerank,
    RRF-fused order) on any failure -- see module docstring for why this
    degrades gracefully instead of propagating, unlike the chat guardrail's
    classifier."""
    if len(chunks) <= 1:
        return chunks[:top_k]

    try:
        client = get_chat_client(settings)
        listing = "\n\n".join(f"[{index + 1}] {chunk.content}" for index, chunk in enumerate(chunks))
        response = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM_PROMPT},
                {"role": "user", "content": f"Question: {query}\n\nCandidate passages:\n\n{listing}"},
            ],
            max_tokens=500,
            reasoning_effort="low",
            temperature=0,
        )
        raw = (response.choices[0].message.content or "").strip()
        order = [int(token) for token in re.findall(r"\d+", raw)]

        seen: set[int] = set()
        reranked: list[RetrievedChunk] = []
        for position in order:
            if 1 <= position <= len(chunks) and position not in seen:
                seen.add(position)
                reranked.append(chunks[position - 1])
        # Safety net: append any candidates the reranker didn't mention
        # (malformed/partial response), preserving their original order, so
        # a parsing gap never silently drops a relevant chunk.
        for index, chunk in enumerate(chunks):
            if (index + 1) not in seen:
                reranked.append(chunk)

        return reranked[:top_k]
    except Exception:
        logger.exception("Reranking failed; falling back to pre-rerank hybrid-search order")
        return chunks[:top_k]


async def retrieve_chunks(
    db: Client,
    user_id: UUID,
    query: str,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    """Frozen signature (design.md DD-2). Returns the top `top_k` chunks owned
    by `user_id` most relevant to `query` (hybrid vector + keyword search,
    RRF-fused, then LLM-reranked -- see module docstring), or `[]` on any
    failure or when nothing relevant is found."""
    settings: Settings = get_settings()

    try:
        query_embedding = await embed_text(query, settings)
    except Exception:
        logger.exception("Embedding failed during chat-time retrieval; treating as a retrieval miss")
        return []

    candidate_count = min(top_k * _CANDIDATE_POOL_MULTIPLIER, _MAX_CANDIDATE_POOL)
    try:
        response = db.rpc(
            "match_chunks_hybrid",
            {
                "query_embedding": query_embedding,
                "query_text": query,
                "match_user": str(user_id),
                "match_count": candidate_count,
            },
        ).execute()
    except Exception:
        logger.exception("match_chunks_hybrid RPC failed during chat-time retrieval; treating as a retrieval miss")
        return []

    rows = response.data or []
    candidates = [RetrievedChunk(**row) for row in rows]
    if not candidates:
        return []

    return await _rerank_chunks(query, candidates, settings, top_k)
