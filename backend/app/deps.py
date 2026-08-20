"""
Auth + Supabase client dependencies (design.md: "Module Boundaries" ->
backend/app/deps.py owner: backend-1, G-4).

Two client factories are exposed:

- `get_db(...)` — a FastAPI dependency returning a **request-scoped** Supabase
  client whose PostgREST calls carry the caller's verified JWT as the
  `Authorization` bearer token. This is what makes `auth.uid()` resolve inside
  Postgres and RLS the primary isolation boundary (design.md "Row-Level
  Security" section). This is the client G-5a/G-5b should use for all normal
  reads/writes.

- `get_service_client()` — a **service-role** client, NOT request-scoped, NOT
  tied to a caller. Per design.md this must be used ONLY for the Storage-delete
  and Realtime-status-write paths (both owned by backend-2, G-5b), and those
  writes must always set `user_id` explicitly since RLS is bypassed by the
  service role.

`get_current_user` verifies the incoming Supabase JWT (HS256, `SUPABASE_JWT_SECRET`)
and returns the authenticated principal. Missing/invalid token -> 401
`{"error": ..., "code": "unauthorized"}` per the API contract's standard error
envelope.
"""
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from supabase import Client, create_client

from app.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str | None
    claims: dict


def _unauthorized(detail: str = "Missing or invalid authentication token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": detail, "code": "unauthorized"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Verify the Supabase JWT from `Authorization: Bearer <token>` and return the
    authenticated caller. Raises 401 on missing/invalid/expired tokens (FR-AUTH-2)."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    token = credentials.credentials
    try:
        claims = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise _unauthorized("Token missing a valid subject") from exc

    return CurrentUser(id=user_id, email=claims.get("email"), claims=claims)


def _raw_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not credentials.credentials:
        raise _unauthorized()
    return credentials.credentials


async def get_db(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> Client:
    """Request-scoped Supabase client. Uses the anon key for the client itself but
    forwards the caller's verified JWT on every PostgREST request, so `auth.uid()`
    resolves inside Postgres and RLS applies. This is the ONLY client G-5a/G-5b
    should use for normal per-user reads/writes."""
    token = _raw_token(credentials)
    # Fail fast on an invalid token before handing back a client (keeps the 401
    # contract identical whether a caller only reaches for `get_db` and not
    # `get_current_user`).
    try:
        jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(token)
    return client


@lru_cache
def _service_client_singleton(url: str, service_role_key: str) -> Client:
    return create_client(url, service_role_key)


def get_service_client(settings: Settings = Depends(get_settings)) -> Client:
    """Service-role Supabase client. NOT request-scoped, bypasses RLS. Restricted by
    design.md to: Supabase Storage delete, and Realtime `document` status writes.
    Callers using this client MUST set `user_id` explicitly on every write."""
    return _service_client_singleton(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


async def get_current_user_id(user: CurrentUser = Depends(get_current_user)) -> UUID:
    """Convenience dependency for handlers that only need the caller's id."""
    return user.id


def request_bearer_token(request: Request) -> str | None:
    """Non-dependency helper for code paths (e.g. SSE generators) that need the raw
    bearer token outside FastAPI's dependency-injection request/response cycle."""
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    return auth_header.split(" ", 1)[1].strip()
