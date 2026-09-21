"""Tests for `app.deps` JWT verification (G-10a: ES256/JWKS).

The real hosted Supabase project signs access tokens with ES256 against an
asymmetric (EC P-256) key published at the project's JWKS endpoint. These tests
mint an in-test EC P-256 keypair, publish it as a mock JWKS (by monkeypatching
`PyJWKClient.fetch_data`, the one place `app.deps` reaches out over the network),
and exercise `get_current_user` / `get_db` against ES256-signed tokens end to end
-- no real network call is made.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm

from app.config import Settings
from app.deps import get_current_user, get_db

SUPABASE_URL = "http://localhost:54321"
ISSUER = f"{SUPABASE_URL}/auth/v1"
KID = "test-kid-1"

# A single EC P-256 keypair, reused across tests in this module.
_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_JWK = ECAlgorithm.to_jwk(_PRIVATE_KEY.public_key(), as_dict=True)
_PUBLIC_JWK.update({"kid": KID, "use": "sig", "alg": "ES256"})

# A second, unrelated keypair -- used to mint a token whose signature does NOT
# match anything in the published JWKS (wrong signing key case).
_OTHER_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


def _mock_jwks() -> dict:
    return {"keys": [_PUBLIC_JWK]}


@pytest.fixture(autouse=True)
def _mock_jwks_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the real `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`
    endpoint: monkeypatch `PyJWKClient.fetch_data` (the only network call
    `app.deps` makes) to return our in-test JWKS instead. Also clears
    `app.deps`'s module-level `lru_cache`d JWKS client between tests so no
    fetched-key-set state leaks across tests."""
    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda self: _mock_jwks())

    from app import deps as deps_module

    deps_module._jwks_client.cache_clear()
    yield
    deps_module._jwks_client.cache_clear()


def _make_es256_token(
    sub: str = "11111111-1111-1111-1111-111111111111",
    exp_delta_seconds: int = 3600,
    kid: str = KID,
    aud: str = "authenticated",
    iss: str = ISSUER,
    private_key=_PRIVATE_KEY,
) -> str:
    payload = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "email": "user@example.com",
        "exp": datetime.now(tz=timezone.utc) + timedelta(seconds=exp_delta_seconds),
    }
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


class _FakeCreds:
    def __init__(self, token: str) -> None:
        self.credentials = token


# supabase-py's create_client() validates that the API key is JWT-shaped, so
# tests use JWT-shaped (but not necessarily verifiable) placeholder keys.
_FAKE_ANON_KEY = jwt.encode({"role": "anon"}, "anon-signing-secret", algorithm="HS256")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        SUPABASE_URL=SUPABASE_URL,
        SUPABASE_ANON_KEY=_FAKE_ANON_KEY,
        SUPABASE_SERVICE_ROLE_KEY="service",
        SUPABASE_JWT_SECRET="unused-legacy-hs256-secret-not-checked-anymore",
    )


@pytest.mark.asyncio
async def test_get_current_user_valid_es256_token() -> None:
    token = _make_es256_token()
    user = await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert str(user.id) == "11111111-1111-1111-1111-111111111111"
    assert user.email == "user@example.com"


@pytest.mark.asyncio
async def test_get_current_user_missing_credentials_401() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None, settings=_settings())
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_get_current_user_expired_token_401() -> None:
    token = _make_es256_token(exp_delta_seconds=-10)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_unknown_kid_401() -> None:
    """A token whose `kid` header does not match any key in the JWKS (e.g. a
    stale token from before a key rotation) must be rejected, not crash."""
    token = _make_es256_token(kid="some-other-kid-not-in-jwks")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_wrong_signing_key_401() -> None:
    """A token that claims the right `kid` but was actually signed with a
    different private key (signature does not verify against the published
    public key) must be rejected."""
    token = _make_es256_token(private_key=_OTHER_PRIVATE_KEY)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_wrong_audience_401() -> None:
    token = _make_es256_token(aud="some-other-audience")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_wrong_issuer_401() -> None:
    token = _make_es256_token(iss="http://not-our-project.supabase.co/auth/v1")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_hs256_token() -> None:
    """The legacy HS256/shared-secret path must no longer be accepted at all --
    an HS256-signed token (even one an attacker could forge if they somehow
    knew a guessable secret) must not be accepted as ES256's `algorithms=`
    allowlist excludes it."""
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": datetime.now(tz=timezone.utc) + timedelta(hours=1),
    }
    token = jwt.encode(payload, "some-secret", algorithm="HS256", headers={"kid": KID})
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_db_forwards_bearer_token_to_postgrest() -> None:
    token = _make_es256_token()
    client = await get_db(credentials=_FakeCreds(token), settings=_settings())
    # postgrest-py stores the bearer token in its session headers once .auth() is called
    assert client.postgrest.session.headers.get("Authorization") == f"Bearer {token}"


@pytest.mark.asyncio
async def test_get_db_rejects_invalid_token_401() -> None:
    token = _make_es256_token(exp_delta_seconds=-10)
    with pytest.raises(HTTPException) as exc_info:
        await get_db(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401
