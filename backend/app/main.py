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

NOTE (G-9 integration): both `threads` (G-5a) and `documents` (G-5b) routers
are registered below -- this is the resolved union of the parallel-branch
router-registration seam described above, per design.md's "DevOps merges both
at the deploy gate" and docs/INTEGRATION.md.
"""
import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import get_settings
from app.routers import documents, health, metadata_fields, settings, threads

logger = logging.getLogger("knsense")

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

    app = FastAPI(title="KnSense API", version="0.1.0")

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

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        """Normalizes every `HTTPException` raised anywhere in the app to
        one flat `{"error": str, "code": str}` response body -- the single
        error-response convention for this entire API.

        Before this handler existed, FastAPI's own default behavior wrapped
        whatever `detail` a route passed under a `"detail"` key
        (`{"detail": {"error": ..., "code": ...}}`), a different shape than
        threads.py's custom JSONResponse-based error helpers already
        returned flat. Every frontend caller had to guess which shape a
        given error response would use. This handler removes the need for
        that guess: `detail={"error": ..., "code": ...}` (the convention
        used throughout documents.py/metadata_fields.py/deps.py) is
        unwrapped to the flat shape directly; a plain string `detail`
        (FastAPI's own default for framework-level errors) becomes
        `{"error": <that string>, "code": "error"}`."""
        if isinstance(exc.detail, dict):
            content = {
                "error": exc.detail.get("error", "An error occurred"),
                "code": exc.detail.get("code", "error"),
            }
        else:
            content = {"error": str(exc.detail), "code": "error"}
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Reshapes FastAPI's default 422 response (`detail` as a list of
        `{loc, msg, type}` objects -- a third, structurally different shape)
        into the same flat `{"error", "code"}` convention as every other
        error response in this app."""
        message = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": message or "Invalid request.", "code": "invalid_request"},
        )

    app.include_router(health.router)

    # --- Router registration seam (resolved at G-9 integration) ---
    # G-5a (backend-1) registers thread/message/chat endpoints:
    app.include_router(threads.router)
    # G-5b (backend-2) registers document upload/list/delete endpoints:
    app.include_router(documents.router)
    # Chat guardrail settings (restrict-to-documents toggle + threshold):
    app.include_router(settings.router)
    # Module 4: configurable metadata field definitions:
    app.include_router(metadata_fields.router)

    return app


app = create_app()
