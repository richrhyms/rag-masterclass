"""
Chat/LLM service (design.md "Module Boundaries": `backend/app/services/chat.py`,
owner: backend-1, G-5a). Owns:

  - The Module 2 Chat Completions integration -- the ONLY delivered chat
    runtime after DD-1 (Replace, confirmed at G-3). The Module 1 OpenAI
    Responses API path was built and traced against a live LangSmith project
    as the G-5a milestone and has since been removed from this file entirely
    (no runtime selector, no dual persistence -- AC-BE-8). See the G-5a gate
    file / PR commit history for the pre-Replace milestone commit.
  - App-managed conversation memory (FR-BE-5b): prior thread messages are
    loaded fresh on every request (stateless -- no server-side session state)
    and included in the Chat Completions message array.
  - pgvector grounding via `app.services.retrieval.retrieve_chunks` (DD-2):
    retrieved chunk content is injected into the system/context portion of
    the message array; a retrieval miss proceeds un-grounded (AC-CHAT-4b).
  - SSE event streaming per the frozen SSE event contract (design.md "SSE
    event contract"): `start` -> `token`* -> exactly one terminal event
    (`done` or `error`).
  - LangSmith tracing (FR-OBS-1/2), via the client wiring in `services/llm.py`
    (graceful no-op when LangSmith is not configured).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID

from supabase import Client

from app.config import Settings
from app.models import RetrievedChunk
from app.services.llm import LLMConfigError, get_chat_client
from app.services.retrieval import retrieve_chunks

logger = logging.getLogger("rag_masterclass.chat")

_SYSTEM_PROMPT = (
    "You are a helpful assistant in a RAG (retrieval-augmented generation) "
    "application. Answer the user's questions using the conversation so far. "
    "When relevant context from the user's ingested documents is provided "
    "below, ground your answer in it and prefer it over general knowledge. "
    "If no context is provided, or none of it is relevant to the question, "
    "answer from general knowledge instead -- never refuse to answer just "
    "because no ingested context was found."
)


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame per the frozen event contract: a named
    `event:` line and a JSON `data:` line, terminated by a blank line."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _build_grounding_context(chunks: list[RetrievedChunk]) -> str | None:
    if not chunks:
        return None
    parts = [
        f"[Source {index + 1} (relevance {chunk.score:.2f})]\n{chunk.content}"
        for index, chunk in enumerate(chunks)
    ]
    return "Relevant context retrieved from the user's ingested documents:\n\n" + "\n\n".join(parts)


def _load_history(db: Client, thread_id: UUID, exclude_message_id: UUID) -> list[dict]:
    """App-managed memory (FR-BE-5b): every prior message in the thread,
    ordered oldest-first by `message(thread_id, created_at)`, excluding the
    just-persisted current user message (it is appended separately, after
    the freshly-assembled grounding context, so grounding always reflects
    the latest query)."""
    response = (
        db.table("message")
        .select("id,role,content")
        .eq("thread_id", str(thread_id))
        .order("created_at")
        .execute()
    )
    rows = response.data or []
    return [
        {"role": row["role"], "content": row["content"]}
        for row in rows
        if row["id"] != str(exclude_message_id)
    ]


def _persist_assistant_message(db: Client, thread_id: UUID, user_id: UUID, content: str) -> UUID:
    response = (
        db.table("message")
        .insert(
            {
                "thread_id": str(thread_id),
                "user_id": str(user_id),
                "role": "assistant",
                "content": content,
            }
        )
        .execute()
    )
    return UUID(response.data[0]["id"])


def _touch_thread(db: Client, thread_id: UUID) -> None:
    """Bump `thread.updated_at` so GET /api/threads ordering (updated_at desc)
    reflects the latest activity. No DB trigger exists for this (the schema is
    frozen from G-4), so it is done explicitly at the application layer on
    every new message -- see also `routers/threads.py`, which does the same
    after persisting the user message."""
    db.table("thread").update({"updated_at": datetime.now(timezone.utc).isoformat()}).eq(
        "id", str(thread_id)
    ).execute()


async def generate_chat_stream(
    *,
    db: Client,
    settings: Settings,
    user_id: UUID,
    thread_id: UUID,
    user_message_id: UUID,
    user_message_content: str,
) -> AsyncIterator[str]:
    """Async generator of SSE-formatted frames for `POST
    /api/threads/{thread_id}/chat`. The caller (router) is responsible for
    thread-ownership/empty-message validation and persisting the user message
    *before* calling this (pre-stream errors are plain HTTP responses, not
    part of this stream). This generator:

      1. emits `start`
      2. loads prior thread history (app-managed memory)
      3. runs chat-time retrieval (DD-2) and builds a grounded system prompt
      4. streams the Chat Completions response, emitting one `token` per delta
      5. persists the completed assistant message and bumps `thread.updated_at`
      6. emits exactly one terminal event: `done` on success, `error` otherwise

    A retrieval miss never triggers the `error` terminal event (AC-CHAT-4b) --
    only LLM-provider/config/streaming failures do.
    """
    yield _sse("start", {"thread_id": str(thread_id), "user_message_id": str(user_message_id)})

    try:
        history = _load_history(db, thread_id, exclude_message_id=user_message_id)

        try:
            chunks = await retrieve_chunks(db, user_id, user_message_content, top_k=settings.RETRIEVAL_TOP_K)
        except Exception:
            # retrieve_chunks already treats its own embedding/RPC failures as
            # `[]` (DD-2); this is a belt-and-suspenders guard so that no
            # unexpected exception escaping it can ever turn a retrieval miss
            # into a failed chat request (AC-CHAT-4b).
            logger.exception("Unexpected error calling retrieve_chunks; proceeding un-grounded")
            chunks = []

        grounding = _build_grounding_context(chunks)
        system_content = _SYSTEM_PROMPT if grounding is None else f"{_SYSTEM_PROMPT}\n\n{grounding}"

        messages = [
            {"role": "system", "content": system_content},
            *history,
            {"role": "user", "content": user_message_content},
        ]

        client = get_chat_client(settings)  # raises LLMConfigError if Module 2 is unconfigured

        content_parts: list[str] = []
        stream = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            stream=True,
        )
        async for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content
            if delta:
                content_parts.append(delta)
                yield _sse("token", {"delta": delta})

        full_content = "".join(content_parts)
        assistant_message_id = _persist_assistant_message(db, thread_id, user_id, full_content)
        _touch_thread(db, thread_id)

        yield _sse(
            "done",
            {"assistant_message_id": str(assistant_message_id), "content": full_content},
        )

    except LLMConfigError as exc:
        logger.error("Chat Completions provider misconfigured: %s", exc)
        yield _sse("error", {"error": str(exc), "code": "server_misconfigured"})
    except Exception:  # noqa: BLE001 - any mid-stream failure must degrade to a terminal SSE event, never an unhandled 500
        logger.exception("Unhandled error during chat streaming")
        yield _sse(
            "error",
            {"error": "An unexpected error occurred while generating a response.", "code": "internal_error"},
        )
