import uuid

import pytest

import app.services.retrieval as retrieval_module
from app.config import Settings
from app.models import RetrievedChunk
from app.services.retrieval import retrieve_chunks
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()


def _settings(**overrides) -> Settings:
    # `_env_file=None` is load-bearing: without it, pydantic-settings would
    # load the real backend/.env's LLM credentials even though every test
    # here mocks `get_chat_client` -- see test_chat_service.py's
    # `_override_settings` for the same pattern and rationale.
    base = dict(
        _env_file=None,
        SUPABASE_URL="http://localhost:54321",
        SUPABASE_ANON_KEY="anon",
        SUPABASE_SERVICE_ROLE_KEY="service",
        SUPABASE_JWT_SECRET="secret",
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_API_KEY="key",
        LLM_MODEL="gpt-4o-mini",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _fake_embed(monkeypatch: pytest.MonkeyPatch):
    async def _embed(_text: str, _settings) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(retrieval_module, "embed_text", _embed)


@pytest.fixture()
def _identity_rerank(monkeypatch: pytest.MonkeyPatch):
    """For the RPC call/candidate-pool mechanics tests (retrieve_chunks),
    which aren't about reranking itself -- a no-op reranker (returns the
    first top_k candidates unchanged) so they don't need to mock an LLM
    client. NOT autouse: the reranking-specific tests below call
    `_rerank_chunks` directly and must exercise the real implementation."""

    async def _identity(_query, chunks, _settings, top_k):
        return chunks[:top_k]

    monkeypatch.setattr(retrieval_module, "_rerank_chunks", _identity)


def _chunk_row(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "document_id": str(uuid.uuid4()),
        "content": "the sky is blue",
        "chunk_index": 0,
        "score": 0.87,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_typed_results_from_hybrid_rpc(_identity_rerank) -> None:
    db = FakeSupabaseClient()
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    captured_calls = []

    def _rpc_handler(name: str, params: dict) -> list[dict]:
        captured_calls.append((name, params))
        return [_chunk_row(id=str(chunk_id), document_id=str(doc_id), content="the sky is blue", score=0.87)]

    db.rpc_handler = _rpc_handler

    results = await retrieve_chunks(db, USER_ID, "what color is the sky?", top_k=5)

    assert len(results) == 1
    assert isinstance(results[0], RetrievedChunk)
    assert results[0].content == "the sky is blue"
    assert results[0].score == 0.87

    [(name, params)] = captured_calls
    assert name == "match_chunks_hybrid"
    assert params["match_user"] == str(USER_ID)
    assert params["query_embedding"] == [0.1, 0.2, 0.3]
    assert params["query_text"] == "what color is the sky?"
    # candidate pool is wider than top_k (so reranking has something to
    # actually narrow down), not equal to it
    assert params["match_count"] == 20  # min(5 * 4, 20)


@pytest.mark.asyncio
async def test_retrieve_chunks_candidate_pool_capped_at_max(_identity_rerank) -> None:
    db = FakeSupabaseClient()
    captured_calls = []

    def _rpc_handler(name: str, params: dict) -> list[dict]:
        captured_calls.append((name, params))
        return []

    db.rpc_handler = _rpc_handler

    await retrieve_chunks(db, USER_ID, "query", top_k=10)

    [(_, params)] = captured_calls
    # 10 * 4 = 40, capped at the 20-candidate ceiling
    assert params["match_count"] == 20


@pytest.mark.asyncio
async def test_retrieve_chunks_empty_result_is_not_an_error() -> None:
    db = FakeSupabaseClient()
    db.rpc_handler = lambda _name, _params: []

    results = await retrieve_chunks(db, USER_ID, "no relevant content exists", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_empty_on_embedding_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_text: str, _settings):
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(retrieval_module, "embed_text", _boom)

    db = FakeSupabaseClient()
    db.rpc_handler = lambda _name, _params: [{"id": str(uuid.uuid4())}]  # should never be reached

    results = await retrieve_chunks(db, USER_ID, "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_empty_on_rpc_failure() -> None:
    db = FakeSupabaseClient()

    def _boom(_name: str, _params: dict) -> list[dict]:
        raise RuntimeError("db unreachable")

    db.rpc_handler = _boom

    results = await retrieve_chunks(db, USER_ID, "query", top_k=5)

    assert results == []


@pytest.mark.asyncio
async def test_retrieve_chunks_truncates_to_top_k_via_reranker(_identity_rerank) -> None:
    """The reranker (mocked as identity here via the autouse fixture) is
    what narrows the wider candidate pool back down to top_k -- confirms
    retrieve_chunks doesn't itself just return the raw RPC rows unbounded."""
    db = FakeSupabaseClient()
    rows = [_chunk_row(content=f"chunk {i}") for i in range(8)]
    db.rpc_handler = lambda _name, _params: rows

    results = await retrieve_chunks(db, USER_ID, "query", top_k=3)

    assert len(results) == 3


# ---------------------------------------------------------------------------
# Reranking (Module 6, PRD)
# ---------------------------------------------------------------------------


class _FakeRerankMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeRerankChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeRerankMessage(content)


class _FakeRerankResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeRerankChoice(content)]


class _FakeRerankCompletions:
    def __init__(self, content: str | None) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRerankResponse(self._content)


class _FakeRerankClient:
    def __init__(self, content: str | None) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeRerankCompletions(content)


def _chunks(n: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(id=uuid.uuid4(), document_id=uuid.uuid4(), content=f"chunk {i}", chunk_index=i, score=0.5)
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_rerank_chunks_reorders_by_llm_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _chunks(3)  # indices 1, 2, 3 in the reranker's 1-based listing
    fake_client = _FakeRerankClient("3,1,2")
    monkeypatch.setattr(retrieval_module, "get_chat_client", lambda _settings: fake_client)

    result = await retrieval_module._rerank_chunks("a question", chunks, _settings(), top_k=3)

    assert [c.content for c in result] == ["chunk 2", "chunk 0", "chunk 1"]
    call = fake_client.chat.completions.calls[0]
    assert call["temperature"] == 0
    assert call["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_rerank_chunks_truncates_to_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _chunks(5)
    fake_client = _FakeRerankClient("5,4,3,2,1")
    monkeypatch.setattr(retrieval_module, "get_chat_client", lambda _settings: fake_client)

    result = await retrieval_module._rerank_chunks("q", chunks, _settings(), top_k=2)

    assert len(result) == 2
    assert [c.content for c in result] == ["chunk 4", "chunk 3"]


@pytest.mark.asyncio
async def test_rerank_chunks_appends_unmentioned_candidates_as_safety_net(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _chunks(3)
    # reranker only mentions passage 2 -- 1 and 3 must still not be silently dropped
    fake_client = _FakeRerankClient("2")
    monkeypatch.setattr(retrieval_module, "get_chat_client", lambda _settings: fake_client)

    result = await retrieval_module._rerank_chunks("q", chunks, _settings(), top_k=3)

    assert [c.content for c in result] == ["chunk 1", "chunk 0", "chunk 2"]


@pytest.mark.asyncio
async def test_rerank_chunks_falls_back_to_original_order_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _chunks(3)

    class _ExplodingCompletions:
        async def create(self, **_kwargs):
            raise RuntimeError("provider network error")

    class _ExplodingClient:
        def __init__(self) -> None:
            self.chat = type("_Chat", (), {})()
            self.chat.completions = _ExplodingCompletions()

    monkeypatch.setattr(retrieval_module, "get_chat_client", lambda _settings: _ExplodingClient())

    result = await retrieval_module._rerank_chunks("q", chunks, _settings(), top_k=2)

    # falls back to the pre-rerank order, truncated to top_k -- a reranking
    # failure must never fail the whole chat turn
    assert [c.content for c in result] == ["chunk 0", "chunk 1"]


@pytest.mark.asyncio
async def test_rerank_chunks_skips_llm_call_for_single_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = _chunks(1)
    fake_client = _FakeRerankClient("1")
    monkeypatch.setattr(retrieval_module, "get_chat_client", lambda _settings: fake_client)

    result = await retrieval_module._rerank_chunks("q", chunks, _settings(), top_k=5)

    assert result == chunks
    assert fake_client.chat.completions.calls == []
