import uuid

import pytest

import app.services.retrieval as retrieval_module
from app.models import RetrievedChunk
from app.services.retrieval import retrieve_chunks
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _fake_embed(monkeypatch: pytest.MonkeyPatch):
    async def _embed(_text: str, _settings) -> list[float]:
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(retrieval_module, "embed_text", _embed)


@pytest.mark.asyncio
async def test_retrieve_chunks_returns_typed_results_from_match_chunks_rpc() -> None:
    db = FakeSupabaseClient()
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    captured_calls = []

    def _rpc_handler(name: str, params: dict) -> list[dict]:
        captured_calls.append((name, params))
        return [
            {
                "id": str(chunk_id),
                "document_id": str(doc_id),
                "content": "the sky is blue",
                "chunk_index": 0,
                "score": 0.87,
            }
        ]

    db.rpc_handler = _rpc_handler

    results = await retrieve_chunks(db, USER_ID, "what color is the sky?", top_k=5)

    assert len(results) == 1
    assert isinstance(results[0], RetrievedChunk)
    assert results[0].content == "the sky is blue"
    assert results[0].score == 0.87

    [(name, params)] = captured_calls
    assert name == "match_chunks"
    assert params["match_user"] == str(USER_ID)
    assert params["match_count"] == 5
    assert params["query_embedding"] == [0.1, 0.2, 0.3]


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
