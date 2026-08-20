import pytest
from openai import AsyncOpenAI

from app.config import Settings
from app.services.llm import LLMConfigError, _maybe_trace, embed_text, get_chat_client, get_embedding_client


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        SUPABASE_URL="http://localhost:54321",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service",
        SUPABASE_JWT_SECRET="secret",
    )
    base.update(overrides)
    return Settings(**base)


def test_get_chat_client_raises_when_unconfigured() -> None:
    settings = _settings(LLM_BASE_URL=None, LLM_API_KEY=None, LLM_MODEL=None)
    with pytest.raises(LLMConfigError):
        get_chat_client(settings)


def test_get_chat_client_builds_client_with_configured_provider() -> None:
    settings = _settings(
        LLM_BASE_URL="https://openrouter.ai/api/v1",
        LLM_API_KEY="or-key",
        LLM_MODEL="some-model",
    )
    client = get_chat_client(settings)
    assert isinstance(client, AsyncOpenAI)
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
    assert client.api_key == "or-key"


def test_get_embedding_client_raises_when_unconfigured() -> None:
    settings = _settings(EMBEDDING_BASE_URL=None, EMBEDDING_API_KEY=None)
    with pytest.raises(LLMConfigError):
        get_embedding_client(settings)


def test_get_embedding_client_builds_client_when_configured() -> None:
    settings = _settings(
        EMBEDDING_BASE_URL="https://api.openai.com/v1",
        EMBEDDING_API_KEY="emb-key",
        EMBEDDING_MODEL="text-embedding-3-small",
    )
    client = get_embedding_client(settings)
    assert isinstance(client, AsyncOpenAI)
    assert client.api_key == "emb-key"


def test_maybe_trace_is_noop_when_langsmith_not_configured() -> None:
    settings = _settings(LANGSMITH_API_KEY=None, LANGSMITH_TRACING=False)
    client = AsyncOpenAI(api_key="x")
    original_create = client.chat.completions.create
    result = _maybe_trace(client, settings)
    assert result is client
    # Bound methods re-fetched from the same instance are `==` but not
    # necessarily `is` the same object; equality is what proves no patching
    # happened (a patched instance would carry a plain function attribute).
    assert result.chat.completions.create == original_create


def test_maybe_trace_wraps_client_when_langsmith_fully_configured() -> None:
    settings = _settings(LANGSMITH_API_KEY="ls-key", LANGSMITH_TRACING=True)
    client = AsyncOpenAI(api_key="x")
    original_create = client.chat.completions.create
    result = _maybe_trace(client, settings)
    # wrap_openai patches the bound method in place, so the wrapped create is
    # a different callable than the original unwrapped one.
    assert result.chat.completions.create is not original_create


def test_maybe_trace_degrades_gracefully_on_wrap_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(LANGSMITH_API_KEY="ls-key", LANGSMITH_TRACING=True)
    client = AsyncOpenAI(api_key="x")

    import langsmith.wrappers

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(langsmith.wrappers, "wrap_openai", _boom)
    result = _maybe_trace(client, settings)
    assert result is client  # never raises; degrades to the unwrapped client


@pytest.mark.asyncio
async def test_embed_text_calls_configured_embedding_client(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        EMBEDDING_BASE_URL="https://api.openai.com/v1",
        EMBEDDING_API_KEY="emb-key",
        EMBEDDING_MODEL="text-embedding-3-small",
    )

    class _FakeEmbeddingItem:
        embedding = [0.1, 0.2, 0.3]

    class _FakeEmbeddingResponse:
        data = [_FakeEmbeddingItem()]

    class _FakeEmbeddings:
        async def create(self, model: str, input: str):  # noqa: A002 - matches SDK signature
            assert model == "text-embedding-3-small"
            assert input == "hello world"
            return _FakeEmbeddingResponse()

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    import app.services.llm as llm_module

    monkeypatch.setattr(llm_module, "get_embedding_client", lambda _settings: _FakeClient())

    vector = await embed_text("hello world", settings)
    assert vector == [0.1, 0.2, 0.3]
