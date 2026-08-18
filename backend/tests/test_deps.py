from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.deps import get_current_user, get_db

SECRET = "test-jwt-secret-at-least-32-characters-long"


def _make_token(sub: str = "11111111-1111-1111-1111-111111111111", exp_delta_seconds: int = 3600) -> str:
    payload = {
        "sub": sub,
        "aud": "authenticated",
        "email": "user@example.com",
        "exp": datetime.now(tz=timezone.utc) + timedelta(seconds=exp_delta_seconds),
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


class _FakeCreds:
    def __init__(self, token: str) -> None:
        self.credentials = token


# supabase-py's create_client() validates that the API key is JWT-shaped, so
# tests use JWT-shaped (but not necessarily verifiable) placeholder keys.
_FAKE_ANON_KEY = jwt.encode({"role": "anon"}, "anon-signing-secret", algorithm="HS256")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        SUPABASE_URL="http://localhost:54321",
        SUPABASE_ANON_KEY=_FAKE_ANON_KEY,
        SUPABASE_SERVICE_ROLE_KEY="service",
        SUPABASE_JWT_SECRET=SECRET,
    )


@pytest.mark.asyncio
async def test_get_current_user_valid_token() -> None:
    token = _make_token()
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
    token = _make_token(exp_delta_seconds=-10)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=_FakeCreds(token), settings=_settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_db_forwards_bearer_token_to_postgrest() -> None:
    token = _make_token()
    client = await get_db(credentials=_FakeCreds(token), settings=_settings())
    # postgrest-py stores the bearer token in its session headers once .auth() is called
    assert client.postgrest.session.headers.get("Authorization") == f"Bearer {token}"
