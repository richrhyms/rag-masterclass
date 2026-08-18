"""
FastAPI app entrypoint (design.md: "Module Boundaries" -> backend/app/main.py,
owner: backend-1, G-4).

Responsibilities scoped to this gate:
  - construct the FastAPI app
  - CORS
  - startup config validation that fails fast on missing required env vars
    (LangSmith is exempt -- it degrades gracefully per FR-OBS-2 and must never
    fail startup)
  - register the health router
  - leave the router-registration seam for G-5a (`routers.threads`) and G-5b
    (`routers.documents`) -- see the commented `include_router` calls below.

This file intentionally implements no business endpoints beyond health.
"""
import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.routers import health

logger = logging.getLogger("rag_masterclass")

# Local-dev CORS origins for the frontend dev server / docker-compose frontend
# container. Not part of the env-var configuration contract (design.md scopes
# that to provider/Supabase/LangSmith settings only), so this is a fixed,
# local-first default rather than a new env var.
_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _validate_config_or_raise() -> None:
    """Fail fast at import/startup time if required env vars are missing.

    `get_settings()` (pydantic-settings) already raises `ValidationError` when a
    required field (SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_JWT_SECRET) has no value and no default -- this function exists to
    surface that failure with a clear, actionable message rather than a raw
    pydantic traceback, and to make the fail-fast behavior explicit and testable.

    LangSmith vars are optional everywhere in Settings and are never validated
    here -- their absence must never prevent startup (FR-OBS-2).
    """
    try:
        get_settings()
    except ValidationError as exc:
        missing = ", ".join(sorted({err["loc"][0] for err in exc.errors() if err.get("loc")}))
        raise RuntimeError(
            "Missing required environment variable(s): "
            f"{missing or exc}. Copy backend/.env.example to backend/.env and fill "
            "in the required values before starting the server."
        ) from exc


def create_app() -> FastAPI:
    _validate_config_or_raise()

    app = FastAPI(title="RAG Masterclass API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RuntimeError)
    async def _config_error_handler(_: Request, exc: RuntimeError) -> JSONResponse:
        # Defensive: create_app() already raises before the app is constructed if
        # config is invalid, so this only guards against RuntimeErrors raised
        # later in the request lifecycle.
        logger.error("Configuration error: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": str(exc), "code": "server_misconfigured"},
        )

    app.include_router(health.router)

    # --- Router registration seam ---
    # G-5a (backend-1) registers thread/message/chat endpoints:
    #   from app.routers import threads
    #   app.include_router(threads.router)
    #
    # G-5b (backend-2) registers document upload/list/delete endpoints:
    #   from app.routers import documents
    #   app.include_router(documents.router)

    return app


app = create_app()
