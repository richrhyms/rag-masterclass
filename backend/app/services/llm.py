"""
Raw OpenAI-compatible SDK client wiring (design.md "Module Boundaries":
`backend/app/services/llm.py`, owner: backend-1, G-5a).

This is the ONLY place API clients are constructed for the chat/embeddings
paths -- no LLM orchestration framework is used anywhere (FR-BE-3, NFR-5);
every call site imports a client from here and calls the raw `openai` SDK
directly.

Provides the Module 2 clients that back the delivered chat runtime:
  - `get_chat_client()`   -> Chat Completions (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL)
  - `get_embedding_client()` -> Embeddings (EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL)
  - `embed_text(...)` -> convenience wrapper used by `services/retrieval.py` (DD-2)

Per DD-1 (confirmed Replace at G-3), the Module 1 OpenAI Responses API client
that was built and traced as the G-5a milestone has been removed from this
file -- the delivered runtime is Chat Completions only (AC-BE-8). See the
G-5a gate file / PR commit history for the pre-Replace milestone.

LangSmith tracing (FR-OBS-1, FR-OBS-2): clients are wrapped via
`langsmith.wrappers.wrap_openai` only when both `LANGSMITH_API_KEY` and
`LANGSMITH_TRACING` are set/true. Absent that configuration, clients are
returned unwrapped and fully functional -- tracing degrades gracefully and
never breaks or blocks a chat request.
"""
from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.config import Settings

logger = logging.getLogger("rag_masterclass.llm")


class LLMConfigError(RuntimeError):
    """Raised when the env vars required for a given provider client are not
    configured. Callers decide how to surface this (e.g. chat.py maps it to a
    terminal SSE `error` event with code `server_misconfigured`; retrieval.py
    catches it and degrades to an empty result per DD-2)."""


def _maybe_trace(client: AsyncOpenAI, settings: Settings) -> AsyncOpenAI:
    """Wrap `client` for LangSmith tracing iff tracing is fully configured.
    Any failure to wrap is logged and swallowed -- tracing must never break
    chat (FR-OBS-2)."""
    if not (settings.LANGSMITH_API_KEY and settings.LANGSMITH_TRACING):
        return client
    try:
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client)
    except Exception:  # noqa: BLE001 - tracing is best-effort, never fatal
        logger.exception("Failed to wrap OpenAI client for LangSmith tracing; continuing untraced")
        return client


def get_chat_client(settings: Settings) -> AsyncOpenAI:
    """Module 2 Chat Completions client against the configured
    OpenAI-compatible provider (NFR-11). Raises `LLMConfigError` if
    LLM_BASE_URL/LLM_API_KEY/LLM_MODEL are not all set."""
    if not (settings.LLM_BASE_URL and settings.LLM_API_KEY and settings.LLM_MODEL):
        raise LLMConfigError(
            "LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL must all be set to use the "
            "Chat Completions path (Module 2)."
        )
    client = AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)
    return _maybe_trace(client, settings)


def get_embedding_client(settings: Settings) -> AsyncOpenAI:
    """Module 2 embeddings client. Raises `LLMConfigError` if
    EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL are not all set."""
    if not (settings.EMBEDDING_BASE_URL and settings.EMBEDDING_API_KEY and settings.EMBEDDING_MODEL):
        raise LLMConfigError(
            "EMBEDDING_BASE_URL, EMBEDDING_API_KEY, and EMBEDDING_MODEL must all be "
            "set to use retrieval embeddings."
        )
    client = AsyncOpenAI(base_url=settings.EMBEDDING_BASE_URL, api_key=settings.EMBEDDING_API_KEY)
    return _maybe_trace(client, settings)


async def embed_text(text: str, settings: Settings) -> list[float]:
    """Embed `text` with the configured EMBEDDING_MODEL. Raises `LLMConfigError`
    (unconfigured) or whatever the SDK raises on a provider-side failure --
    `services/retrieval.py` is responsible for catching both and degrading to
    an empty retrieval result (DD-2); this function itself does not swallow
    errors so other callers can decide their own handling."""
    client = get_embedding_client(settings)
    response = await client.embeddings.create(model=settings.EMBEDDING_MODEL, input=text)
    return list(response.data[0].embedding)
