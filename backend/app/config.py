"""
Environment-variable configuration contract (design.md: "Environment-variable
configuration contract"). Config is env-only; no admin/config UI (FR-BE-9, NFR-8).

Required-vs-optional split matches design.md exactly:
  - required (always): SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_JWT_SECRET, EMBEDDING_DIM (has a documented default of 1536 so it is
    always populated; the migration's `vector(1536)` literal must match this value).
  - required for Module 1 (Responses API path): OPENAI_API_KEY.
  - required for Module 2 (Chat Completions path): LLM_BASE_URL, LLM_API_KEY,
    LLM_MODEL, EMBEDDING_BASE_URL, EMBEDDING_API_KEY, EMBEDDING_MODEL.
  - optional (tunable, has defaults): RETRIEVAL_TOP_K, CHUNK_SIZE, CHUNK_OVERLAP,
    MAX_UPLOAD_BYTES.
  - optional (observability, degrades gracefully per FR-OBS-2): LANGSMITH_API_KEY,
    LANGSMITH_PROJECT, LANGSMITH_TRACING.

Only the four SUPABASE_* vars are treated as hard-required-at-import-time here: they
have no safe default and the app cannot serve a single request without them. Module
1/2 provider vars are intentionally left optional at the settings layer (None) so
that G-4's skeleton (which ships no LLM-calling code yet) can boot for local
smoke-testing; G-5a's chat path is expected to validate their presence before
attempting a provider call.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Supabase (required) ---
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str

    # --- Module 1: OpenAI Responses API ---
    OPENAI_API_KEY: str | None = None

    # --- Module 2: OpenAI-compatible Chat Completions + embeddings ---
    LLM_BASE_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_MODEL: str | None = None
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # --- Embedding dimension (required; must match the migration's vector(N) literal) ---
    EMBEDDING_DIM: int = Field(default=1536)

    # --- Retrieval / chunking tuning (optional) ---
    RETRIEVAL_TOP_K: int = 5
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 150

    # --- Ingestion (optional) ---
    MAX_UPLOAD_BYTES: int = 5_242_880  # 5 MB

    # --- LangSmith observability (optional; absence must never break chat, FR-OBS-2) ---
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str | None = None
    LANGSMITH_TRACING: bool = False


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Raises pydantic.ValidationError (fail-fast) if a
    hard-required var is missing, per FR-BE-9 / the config validation requirement."""
    return Settings()
