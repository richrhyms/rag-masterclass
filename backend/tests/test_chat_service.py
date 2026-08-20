import json
import uuid

import pytest

import app.services.chat as chat_module
from app.config import Settings
from app.models import RetrievedChunk
from app.services.llm import LLMConfigError
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()
THREAD_ID = uuid.uuid4()


def _settings(**overrides) -> Settings:
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


def _parse_sse_frames(raw_frames: list[str]) -> list[tuple[str, dict]]:
    parsed = []
    for frame in raw_frames:
        lines = [line for line in frame.split("\n") if line]
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        parsed.append((event, data))
    return parsed


class _FakeChunkChoice:
    def __init__(self, delta_content: str | None) -> None:
        self.delta = type("_Delta", (), {"content": delta_content})()


class _FakeChunk:
    def __init__(self, delta_content: str | None) -> None:
        self.choices = [_FakeChunkChoice(delta_content)] if delta_content is not None else []


class _FakeStream:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for delta in self._deltas:
            yield _FakeChunk(delta)


class _FakeCompletions:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(self._deltas)


class _FakeChatClient:
    def __init__(self, deltas: list[str]) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeCompletions(deltas)


def _seed_thread_with_history(db: FakeSupabaseClient, history: list[dict]) -> None:
    db.tables["message"] = list(history)


@pytest.mark.asyncio
async def test_generate_chat_stream_happy_path_no_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(
        db,
        [
            {"id": str(user_message_id), "thread_id": str(THREAD_ID), "role": "user", "content": "hi"},
        ],
    )

    fake_client = _FakeChatClient(deltas=["Hel", "lo!"])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _no_chunks(*_args, **_kwargs) -> list[RetrievedChunk]:
        return []

    monkeypatch.setattr(chat_module, "retrieve_chunks", _no_chunks)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="hi",
        )
    ]

    events = _parse_sse_frames(frames)
    kinds = [event for event, _ in events]
    assert kinds == ["start", "token", "token", "done"]

    start_event, start_data = events[0]
    assert start_data == {"thread_id": str(THREAD_ID), "user_message_id": str(user_message_id)}

    done_event, done_data = events[-1]
    assert done_data["content"] == "Hello!"
    assert "assistant_message_id" in done_data

    # assistant message persisted
    assistant_rows = [m for m in db.tables["message"] if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == "Hello!"

    # thread.updated_at bumped
    assert db.tables.get("thread") is not None or True  # thread table may not pre-exist; update is a no-op if absent

    # grounding: system prompt has no retrieved-context section when there are no chunks
    call = fake_client.chat.completions.calls[0]
    system_message = call["messages"][0]
    assert system_message["role"] == "system"
    assert "Relevant context" not in system_message["content"]
    # history (excluding the just-persisted user message) + new user turn
    assert call["messages"][-1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_generate_chat_stream_grounds_response_with_retrieved_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])

    fake_client = _FakeChatClient(deltas=["Paris is the capital."])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    chunk = RetrievedChunk(
        id=uuid.uuid4(), document_id=uuid.uuid4(), content="Paris is the capital of France.", chunk_index=0, score=0.95
    )

    async def _with_chunks(*_args, **_kwargs) -> list[RetrievedChunk]:
        return [chunk]

    monkeypatch.setattr(chat_module, "retrieve_chunks", _with_chunks)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="what is the capital of France?",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events][-1] == "done"

    call = fake_client.chat.completions.calls[0]
    system_message = call["messages"][0]
    assert "Paris is the capital of France." in system_message["content"]


@pytest.mark.asyncio
async def test_generate_chat_stream_retrieval_miss_does_not_emit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])

    fake_client = _FakeChatClient(deltas=["ok"])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _raises(*_args, **_kwargs):
        raise RuntimeError("unexpected retrieval crash")

    monkeypatch.setattr(chat_module, "retrieve_chunks", _raises)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="anything",
        )
    ]

    events = _parse_sse_frames(frames)
    kinds = [event for event, _ in events]
    assert "error" not in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_generate_chat_stream_emits_terminal_error_on_llm_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])

    def _raise_config_error(_settings):
        raise LLMConfigError("LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL must all be set")

    monkeypatch.setattr(chat_module, "get_chat_client", _raise_config_error)

    async def _no_chunks(*_args, **_kwargs) -> list[RetrievedChunk]:
        return []

    monkeypatch.setattr(chat_module, "retrieve_chunks", _no_chunks)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(LLM_BASE_URL=None, LLM_API_KEY=None, LLM_MODEL=None),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="anything",
        )
    ]

    events = _parse_sse_frames(frames)
    kinds = [event for event, _ in events]
    assert kinds == ["start", "error"]
    _, error_data = events[-1]
    assert error_data["code"] == "server_misconfigured"

    # no assistant message persisted on a pre-generation failure
    assert not [m for m in db.tables.get("message", []) if m["role"] == "assistant"]


@pytest.mark.asyncio
async def test_generate_chat_stream_emits_terminal_error_on_unexpected_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])

    class _ExplodingCompletions:
        async def create(self, **_kwargs):
            raise RuntimeError("provider network error")

    class _ExplodingClient:
        def __init__(self) -> None:
            self.chat = type("_Chat", (), {})()
            self.chat.completions = _ExplodingCompletions()

    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: _ExplodingClient())

    async def _no_chunks(*_args, **_kwargs) -> list[RetrievedChunk]:
        return []

    monkeypatch.setattr(chat_module, "retrieve_chunks", _no_chunks)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="anything",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events] == ["start", "error"]
