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

`get_current_user` verifies the incoming Supabase JWT and returns the authenticated
principal. Missing/invalid token -> 401 `{"error": ..., "code": "unauthorized"}` per
the API contract's standard error envelope.

## JWT verification strategy (G-10a fix)

The real hosted Supabase project has asymmetric JWT signing-keys enabled and issues
**ES256**-signed access tokens (confirmed live: token header is
`{"alg": "ES256", "kid": "..."}`, the project's JWKS endpoint publishes exactly one
EC/P-256 verification key under that `kid`). The previous implementation verified
tokens as HS256 against `SUPABASE_JWT_SECRET`, which is a no-op against real tokens
(they are never HS256) and was rejecting every real signed-in user with 401
(QA gate-8, CRITICAL DEFECT 1).

Tokens are now verified as ES256 against the project's published JWKS, fetched from
`${SUPABASE_URL}/auth/v1/.well-known/jwks.json` (confirmed live at this exact path;
the project ref is never hardcoded -- the URL is always derived from the
`SUPABASE_URL` env var). `aud` is validated as `"authenticated"` and `iss` as
`"${SUPABASE_URL}/auth/v1"`, matching a real, freshly-issued Supabase access token
exactly (both confirmed live against the hosted project, not just from docs).

The JWKS key set is cached in-process (`PyJWKClient`, TTL-based; see
`_JWKS_CACHE_TTL_SECONDS`) so normal request traffic does not refetch it every call.
On a `kid` miss (e.g. after Supabase rotates its signing key), `PyJWKClient`
transparently refetches the key set once before giving up, so key rotation does not
require a process restart.
"""
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from supabase import Client, create_client

from app.config import Settings, get_settings

_bearer_scheme = HTTPBearer(auto_error=False)

# How long PyJWKClient keeps a fetched JWKS response in-process before treating it
# as stale and refetching on the next lookup. Independent of, and shorter than, the
# automatic single-retry-on-kid-miss behavior below (that retry always bypasses this
# TTL and fetches fresh, so a genuine key rotation is picked up immediately, not
# after this TTL elapses).
_JWKS_CACHE_TTL_SECONDS = 600  # 10 minutes


def _jwks_url(settings: Settings) -> str:
    """Derive the project's JWKS endpoint from SUPABASE_URL. Never hardcode the
    project ref -- this must work for any Supabase project this backend points at."""
    return f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _expected_issuer(settings: Settings) -> str:
    return f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    """Module-level cached JWKS client -- one per distinct JWKS URL (effectively a
    singleton per SUPABASE_URL in normal operation, mirroring the pattern already
    used for the service-role Supabase client below). `PyJWKClient` itself caches
    the fetched key set for `lifespan` seconds and transparently refetches once on a
    `kid` miss before raising, satisfying the "cache with TTL + refetch-once-on-
    rotation" requirement without hand-rolled cache bookkeeping."""
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=_JWKS_CACHE_TTL_SECONDS)


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


def _verify_token(token: str, settings: Settings) -> dict:
    """Verify a Supabase-issued access token's ES256 signature against the
    project's published JWKS (selecting the key by the token's `kid` header),
    then validate standard claims (`exp`, `aud="authenticated"`,
    `iss="${SUPABASE_URL}/auth/v1"`) exactly as a real Supabase access token sets
    them. Returns the decoded claims on success. Raises 401 (standard error
    envelope) on any failure: missing/malformed token, unknown `kid`, bad
    signature, wrong algorithm, expired token, or a claim mismatch."""
    jwks_client = _jwks_client(_jwks_url(settings))
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
    except (PyJWKClientError, jwt.PyJWTError) as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
            issuer=_expected_issuer(settings),
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"Invalid token: {exc}") from exc

    return claims


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    """Verify the Supabase JWT from `Authorization: Bearer <token>` and return the
    authenticated caller. Raises 401 on missing/invalid/expired tokens (FR-AUTH-2)."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized()

    token = credentials.credentials
    claims = _verify_token(token, settings)

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
    _verify_token(token, settings)

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
