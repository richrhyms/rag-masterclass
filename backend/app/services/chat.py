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
  - The chat guardrail (`chat_setting` singleton, `app/routers/settings.py`):
    when `restrict_to_documents` is on, a question whose best retrieval score
    is below `min_relevance_score` is answered deterministically -- without
    ever calling the LLM -- stating the question is out of scope and listing
    the user's completed document filenames. This is intentionally NOT
    delegated to the model's judgment (a system-prompt instruction alone can
    be talked around); the gate runs before the LLM is invoked at all so an
    out-of-scope question never reaches it. When the gate passes (or the
    guardrail is off), the system prompt still reinforces staying grounded
    as a second line of defense for borderline cases.
  - A second, LLM-based scope check (`_classify_request_in_scope`) that runs
    only when the cheap score gate above *passes* (i.e. retrieval found
    something plausibly relevant). The score gate alone is bypassable by
    "topic laundering": embedding the user's whole message produces one
    blended similarity score, so wrapping an off-topic ask inside on-topic
    framing text can drag the average back above the threshold even though
    part of the request has nothing to do with the retrieved content. This
    check judges the retrieved passages against the actual request with an
    LLM (which can reason about partial coverage, unlike a single cosine
    number) and runs BEFORE the real answer-generation call, so an
    off-topic request is never sent to the model that would otherwise
    answer it -- prevention, not post-generation filtering. If this
    classification call itself fails (provider/network error), that
    failure is deliberately NOT swallowed into a guessed verdict -- it
    propagates to the same terminal `error` SSE event as any other
    provider failure, since a failed call carries no information about the
    request's topic and the answer-generation call right after it would
    hit the same broken provider anyway.
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

_SYSTEM_PROMPT_GROUNDED = (
    "You are a knowledgeable assistant answering questions using the "
    "context retrieved from the user's ingested documents, provided below. "
    "Answer directly and naturally, the way a well-informed person would -- "
    "do not preface your answer with meta-commentary about where the "
    "information came from (e.g. 'Based on the provided documents'), and "
    "do not add disclaimers unless there's a genuine, specific gap in what "
    "the context covers. When the context describes multiple options, "
    "tradeoffs, or perspectives, synthesize them into a clear, direct "
    "answer -- including a real recommendation when asked for one -- "
    "rather than just listing facts and declining to conclude. Stay "
    "grounded in what the context actually supports: don't invent facts it "
    "doesn't contain, and if it truly has nothing relevant to a specific "
    "part of the question, say so plainly rather than guessing."
)

_SYSTEM_PROMPT_OPEN = (
    "You are a helpful assistant in a RAG (retrieval-augmented generation) "
    "application. Answer the user's questions using the conversation so far. "
    "When relevant context from the user's ingested documents is provided "
    "below, ground your answer in it and prefer it over general knowledge. "
    "If no context is provided, or none of it is relevant to the question, "
    "answer from general knowledge instead -- never refuse to answer just "
    "because no ingested context was found."
)


def _load_chat_settings(db: Client) -> tuple[bool, float]:
    """Reads the `chat_setting` singleton (RLS: any authenticated user may
    read). Defaults to the guardrail being ON if the row is somehow missing
    or unreadable -- fail closed, not open, since the whole point of this
    gate is to restrict scope."""
    try:
        response = (
            db.table("chat_setting")
            .select("restrict_to_documents,min_relevance_score")
            .eq("id", True)
            .single()
            .execute()
        )
        row = response.data or {}
        return (
            bool(row.get("restrict_to_documents", True)),
            float(row.get("min_relevance_score", 0.5)),
        )
    except Exception:
        logger.exception("Failed to load chat_setting; defaulting to guardrail ON")
        return True, 0.5


def _has_completed_documents(db: Client, user_id: UUID) -> bool:
    try:
        response = (
            db.table("document")
            .select("id")
            .eq("user_id", str(user_id))
            .eq("status", "completed")
            .limit(1)
            .execute()
        )
        return bool(response.data)
    except Exception:
        logger.exception("Failed to check for completed documents for out-of-scope message")
        return False


def _out_of_scope_message(has_documents: bool) -> str:
    """Deliberately generic for now -- does not name specific documents or
    topics. A per-document filename/topic listing doesn't scale once
    multiple documents (and, later, multi-document selection) are active at
    once: it either becomes an unwieldy list, or requires synthesizing one
    topic description across the whole active document set.

    FUTURE: replace this with a single precomputed "what can I help with"
    summary describing the current active document set as a whole (not
    per-document), regenerated whenever that set changes -- after an
    ingestion completes, after a delete, and later after a document-selection
    change (see chat guardrail discussion). Keep it precomputed/stored rather
    than generated at chat time, so the guardrail gate itself stays a
    zero-LLM-call, deterministic check."""
    if not has_documents:
        return "That's outside what I can help with based on available context -- no context has been provided yet."
    return "That's outside what I can help with based on available context."


_CLASSIFIER_SYSTEM_PROMPT = (
    "You are a strict scope classifier for a document Q&A assistant. You "
    "are given passages retrieved from the user's ingested documents and "
    "the user's message. Decide whether the user's ENTIRE request is "
    "something these passages can help answer. If the message has "
    "multiple parts and ANY part asks about something these passages do "
    "not cover -- even if it is framed as related, phrased as a follow-up, "
    "or wrapped inside an otherwise on-topic question -- classify it as "
    "OFF_TOPIC. Only classify ON_TOPIC if the passages are relevant to the "
    "whole request. Reply with exactly one word, either ON_TOPIC or "
    "OFF_TOPIC, and nothing else -- no punctuation, no explanation."
)


async def _classify_request_in_scope(
    chunks: list[RetrievedChunk], user_message_content: str, settings: Settings
) -> bool:
    """LLM-based second pass for the chat guardrail (see module docstring):
    catches "topic laundering" that the cheap cosine-score gate misses,
    by judging the retrieved passages against the actual request instead
    of one blended similarity number. Only ever called when `chunks` is
    non-empty (the caller already knows the score gate passed).

    Same `reasoning_effort='low'` + generous `max_tokens` pattern as
    `_generate_thread_title`, for the same reason: `gemini-3.6-flash` is a
    "thinking" model that burns its token budget on hidden reasoning and
    returns empty content at low budgets.

    An unparseable-but-successful response defaults to OFF_TOPIC (fail
    closed, consistent with `_load_chat_settings`) -- this is a normal
    control-flow outcome, not an error. An actual exception (provider
    down, network failure, etc.) is deliberately NOT caught here; see the
    module docstring for why it must propagate instead of being treated as
    a scope verdict."""
    client = get_chat_client(settings)
    passages = "\n\n".join(
        f"[Passage {index + 1}]\n{chunk.content}" for index, chunk in enumerate(chunks)
    )
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": _CLASSIFIER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Retrieved passages:\n\n{passages}\n\nUser's message:\n{user_message_content}",
            },
        ],
        max_tokens=500,
        reasoning_effort="low",
    )
    verdict = (response.choices[0].message.content or "").strip().upper()
    return verdict.startswith("ON_TOPIC")


async def _out_of_scope_response(db: Client, user_id: UUID, thread_id: UUID) -> AsyncIterator[str]:
    """Shared terminal response for both guardrail checks (score gate and
    LLM classifier) in `generate_chat_stream` -- persists the same
    deterministic out-of-scope message and emits the same SSE frames
    regardless of which check caught it."""
    full_content = _out_of_scope_message(_has_completed_documents(db, user_id))
    assistant_message_id = _persist_assistant_message(db, thread_id, user_id, full_content)
    _touch_thread(db, thread_id)
    yield _sse("token", {"delta": full_content})
    yield _sse(
        "done",
        {"assistant_message_id": str(assistant_message_id), "content": full_content},
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


async def _generate_thread_title(user_message: str, settings: Settings) -> str:
    """One short, cheap LLM call to turn the user's opening message into a
    real conversation title -- e.g. 'Solar Battery Types' instead of the
    frontend's 'New Conversation' placeholder, matching how mainstream AI
    chat UIs title threads. Titled off the user's message alone (not the
    assistant's reply) so this works identically whether the first turn was
    answered normally or hit the out-of-scope guardrail.

    `reasoning_effort='low'` and a real `max_tokens` budget (not the tiny
    ~20 you'd expect a 3-6 word title to need) are both load-bearing here,
    not tuning: `gemini-3.6-flash` is a "thinking" model whose internal
    reasoning consumes the token budget before any visible output -- without
    both of these, this call was empirically observed to return EMPTY
    content (`finish_reason: 'length'`, `completion_tokens: 0`) or take
    45-70+ seconds even for this trivial a task. `reasoning_effort='low'`
    cuts that down substantially but latency is still genuinely variable
    (observed 3-70s across identical calls) -- see `_maybe_set_thread_title`
    for why that variability is safe to accept here."""
    client = get_chat_client(settings)
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Generate a short, specific title (3-6 words) summarizing what this "
                    "conversation is about, based on the user's message below. Reply with "
                    "ONLY the title -- no quotes, no trailing punctuation, no preamble."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        max_tokens=500,
        reasoning_effort="low",
    )
    title = (response.choices[0].message.content or "").strip().strip("\"'")
    return title[:100]


async def _maybe_set_thread_title(db: Client, settings: Settings, thread_id: UUID, user_message_content: str) -> None:
    """Sets `thread.title` from the user's opening message, but only while
    the thread doesn't have one yet (`thread.title IS NULL`).

    Deliberately checks `thread.title` rather than "is history empty":
    this runs as a `BackgroundTask` attached to the chat endpoint's
    `StreamingResponse` (see `generate_chat_stream`'s call site in
    `routers/threads.py`), which only starts AFTER the full turn --
    including persisting the assistant's reply -- has already completed.
    By that point a message-count/history check would always see at least
    one message (the reply that was just persisted), incorrectly reading
    as "not the first turn" on every single thread. Checking the title
    itself sidesteps that ordering entirely, and as a bonus makes titling
    self-healing: if an earlier attempt failed (title still null), a later
    turn will opportunistically retry rather than being permanently stuck
    with the placeholder.

    Titling runs as a background task (not inline in the generator)
    because its latency is observed to vary wildly (3-70+ seconds, see
    `_generate_thread_title`) -- running it inline would have kept the
    HTTP connection (and therefore the frontend's "sending..." state) open
    for that whole variable window even though the visible answer had
    already arrived.

    Best-effort: any failure here is logged and swallowed -- a thread
    simply keeps its `null` title (frontend falls back to 'New
    Conversation') if titling fails; it must never surface as a user-facing
    error, since by the time this runs the chat turn has already
    succeeded."""
    try:
        response = db.table("thread").select("title").eq("id", str(thread_id)).single().execute()
        if (response.data or {}).get("title"):
            return
        title = await _generate_thread_title(user_message_content, settings)
        if title:
            db.table("thread").update({"title": title}).eq("id", str(thread_id)).execute()
    except Exception:
        logger.exception("Failed to generate/persist thread title for %s", thread_id)


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
      3. runs chat-time retrieval (DD-2)
      4. if the guardrail is on and retrieval's best score is below the
         configured threshold, answers deterministically out-of-scope
         WITHOUT calling the LLM, then skips straight to step 6
      5. if the guardrail is on and step 4 passed, runs the LLM-based scope
         classifier (`_classify_request_in_scope`) against the retrieved
         passages; if it says the request is off-topic, answers the same
         deterministic out-of-scope message and skips straight to step 6
         (see module docstring for why this second check exists)
      6. otherwise builds a grounded system prompt and streams the Chat
         Completions response, emitting one `token` per delta
      7. persists the completed assistant message and bumps `thread.updated_at`,
         then emits exactly one terminal event: `done` on success, `error`
         otherwise

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

        restrict_to_documents, min_relevance_score = _load_chat_settings(db)
        best_score = max((chunk.score for chunk in chunks), default=0.0)

        if restrict_to_documents and best_score < min_relevance_score:
            async for frame in _out_of_scope_response(db, user_id, thread_id):
                yield frame
            return

        if restrict_to_documents and chunks and not await _classify_request_in_scope(
            chunks, user_message_content, settings
        ):
            async for frame in _out_of_scope_response(db, user_id, thread_id):
                yield frame
            return

        grounding = _build_grounding_context(chunks)
        base_prompt = _SYSTEM_PROMPT_GROUNDED if restrict_to_documents else _SYSTEM_PROMPT_OPEN
        system_content = base_prompt if grounding is None else f"{base_prompt}\n\n{grounding}"

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
