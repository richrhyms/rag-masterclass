import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_supabase_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "secret")

    settings = Settings(_env_file=None)

    assert settings.EMBEDDING_DIM == 1536
    assert settings.EMBEDDING_MODEL == "text-embedding-3-small"
    assert settings.RETRIEVAL_TOP_K == 5
    assert settings.CHUNK_SIZE == 1000
    assert settings.CHUNK_OVERLAP == 150
    assert settings.MAX_UPLOAD_BYTES == 5_242_880
    assert settings.LANGSMITH_TRACING is False
    assert settings.OPENAI_API_KEY is None
    assert settings.LLM_BASE_URL is None
