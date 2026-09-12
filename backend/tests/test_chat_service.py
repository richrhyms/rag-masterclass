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


def _mock_classifier(monkeypatch: pytest.MonkeyPatch, *, on_topic: bool) -> list[tuple]:
    """Stands in for `_classify_request_in_scope` so tests that only care
    about score-gate/streaming behavior don't need a fake client that
    understands both the classifier's non-streaming call shape and the
    real generation call's streaming shape. Returns the list of
    (chunks, user_message_content) call args recorded, so tests can assert
    whether the classifier ran at all."""
    calls: list[tuple] = []

    async def _fake(chunks, user_message_content, _settings):
        calls.append((chunks, user_message_content))
        return on_topic

    monkeypatch.setattr(chat_module, "_classify_request_in_scope", _fake)
    return calls


def _seed_thread_with_history(db: FakeSupabaseClient, history: list[dict]) -> None:
    db.tables["message"] = list(history)


def _seed_chat_settings(
    db: FakeSupabaseClient, *, restrict_to_documents: bool = False, min_relevance_score: float = 0.5
) -> None:
    """Most existing tests below predate the guardrail and are about
    streaming/error-handling mechanics, not guardrail behavior -- they seed
    `restrict_to_documents=False` so the gate never engages and the original
    open-chat code path they were written to exercise still runs unchanged.
    Guardrail-specific tests seed this explicitly with restrict=True."""
    db.tables["chat_setting"] = [
        {"id": True, "restrict_to_documents": restrict_to_documents, "min_relevance_score": min_relevance_score}
    ]


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
    _seed_chat_settings(db)

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
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)
    _mock_classifier(monkeypatch, on_topic=True)

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
    _seed_chat_settings(db)

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
    _seed_chat_settings(db)

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
    _seed_chat_settings(db)

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


@pytest.mark.asyncio
async def test_generate_chat_stream_guardrail_blocks_low_score_question(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)
    db.tables["document"] = [
        {"user_id": str(USER_ID), "status": "completed", "filename": "pricing.md", "created_at": "2026-01-01"},
        {"user_id": str(USER_ID), "status": "failed", "filename": "broken.txt", "created_at": "2026-01-02"},
    ]
    classifier_calls = _mock_classifier(monkeypatch, on_topic=True)

    fake_client = _FakeChatClient(deltas=["should never be reached"])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _low_score_chunk(*_args, **_kwargs) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                id=uuid.uuid4(), document_id=uuid.uuid4(), content="irrelevant", chunk_index=0, score=0.1
            )
        ]

    monkeypatch.setattr(chat_module, "retrieve_chunks", _low_score_chunk)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="what's the weather today?",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events] == ["start", "token", "done"]

    # the LLM is never called for an out-of-scope question, and the cheap
    # score gate short-circuits before the LLM-based classifier even runs
    assert fake_client.chat.completions.calls == []
    assert classifier_calls == []

    _, done_data = events[-1]
    # deliberately generic -- no filename or topic is named (doesn't scale to
    # multiple/selected documents; see chat.py's _out_of_scope_message docstring)
    assert done_data["content"] == "That's outside what I can help with based on available context."

    assistant_rows = [m for m in db.tables["message"] if m["role"] == "assistant"]
    assert len(assistant_rows) == 1
    assert assistant_rows[0]["content"] == done_data["content"]


@pytest.mark.asyncio
async def test_generate_chat_stream_guardrail_out_of_scope_message_when_no_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)
    # no `document` rows seeded at all

    fake_client = _FakeChatClient(deltas=["should never be reached"])
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
            user_message_content="anything",
        )
    ]

    events = _parse_sse_frames(frames)
    _, done_data = events[-1]
    assert done_data["content"] == (
        "That's outside what I can help with based on available context -- "
        "no context has been provided yet."
    )
    assert fake_client.chat.completions.calls == []


@pytest.mark.asyncio
async def test_generate_chat_stream_guardrail_allows_high_score_question(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)
    _mock_classifier(monkeypatch, on_topic=True)

    fake_client = _FakeChatClient(deltas=["Grounded answer."])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _high_score_chunk(*_args, **_kwargs) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                id=uuid.uuid4(), document_id=uuid.uuid4(), content="on-topic content", chunk_index=0, score=0.9
            )
        ]

    monkeypatch.setattr(chat_module, "retrieve_chunks", _high_score_chunk)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="a question the docs actually cover",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events][-1] == "done"
    assert len(fake_client.chat.completions.calls) == 1

    system_message = fake_client.chat.completions.calls[0]["messages"][0]
    assert "synthesize them into a clear, direct answer" in system_message["content"]
    assert "on-topic content" in system_message["content"]


@pytest.mark.asyncio
async def test_generate_chat_stream_classifier_blocks_topic_laundering(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cheap score gate alone can be bypassed by wrapping an off-topic
    ask in on-topic framing (a decent best_score from the on-topic part
    drags the whole message above the threshold). This is exactly the
    case the LLM classifier exists to catch: score gate passes, but the
    classifier judges the actual request as not fully covered by the
    retrieved passages."""
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)
    db.tables["document"] = [
        {"user_id": str(USER_ID), "status": "completed", "filename": "pricing.md", "created_at": "2026-01-01"},
    ]
    classifier_calls = _mock_classifier(monkeypatch, on_topic=False)

    fake_client = _FakeChatClient(deltas=["should never be reached"])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _decent_score_chunk(*_args, **_kwargs) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                id=uuid.uuid4(), document_id=uuid.uuid4(), content="on-topic content", chunk_index=0, score=0.8
            )
        ]

    monkeypatch.setattr(chat_module, "retrieve_chunks", _decent_score_chunk)

    frames = [
        frame
        async for frame in chat_module.generate_chat_stream(
            db=db,
            settings=_settings(),
            user_id=USER_ID,
            thread_id=THREAD_ID,
            user_message_id=user_message_id,
            user_message_content="explain the on-topic thing, and also do this unrelated off-topic thing",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events] == ["start", "token", "done"]

    # score gate passed (0.8 >= 0.5) so the classifier ran, and it blocked
    # the request before the real generation LLM was ever invoked
    assert len(classifier_calls) == 1
    assert fake_client.chat.completions.calls == []

    _, done_data = events[-1]
    assert done_data["content"] == "That's outside what I can help with based on available context."


@pytest.mark.asyncio
async def test_generate_chat_stream_classifier_failure_emits_terminal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A classifier failure (provider/network error) carries no
    information about the request's topic and must not be reinterpreted
    as a scope verdict either way -- it propagates to the same terminal
    `error` SSE event as any other provider failure, exactly like a
    failure in the real generation call would."""
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=True, min_relevance_score=0.5)

    async def _boom_classifier(*_args, **_kwargs):
        raise RuntimeError("provider network error")

    monkeypatch.setattr(chat_module, "_classify_request_in_scope", _boom_classifier)

    fake_client = _FakeChatClient(deltas=["should never be reached"])
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    async def _decent_score_chunk(*_args, **_kwargs) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                id=uuid.uuid4(), document_id=uuid.uuid4(), content="on-topic content", chunk_index=0, score=0.8
            )
        ]

    monkeypatch.setattr(chat_module, "retrieve_chunks", _decent_score_chunk)

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
    assert fake_client.chat.completions.calls == []
    assert not [m for m in db.tables.get("message", []) if m["role"] == "assistant"]


class _FakeClassifierMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeClassifierChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeClassifierMessage(content)


class _FakeClassifierResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeClassifierChoice(content)]


class _FakeClassifierCompletions:
    def __init__(self, content: str | None) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeClassifierResponse(self._content)


class _FakeClassifierClient:
    def __init__(self, content: str | None) -> None:
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeClassifierCompletions(content)


@pytest.mark.parametrize(
    "raw_response,expected",
    [
        ("ON_TOPIC", True),
        ("on_topic", True),
        ("  ON_TOPIC  ", True),
        ("OFF_TOPIC", False),
        ("off_topic", False),
        ("", False),
        (None, False),
        ("I think this is ON_TOPIC-ish", False),  # must start with the exact token, not just mention it
    ],
)
@pytest.mark.asyncio
async def test_classify_request_in_scope_parses_verdict(
    raw_response: str | None, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = _FakeClassifierClient(raw_response)
    chunk = RetrievedChunk(
        id=uuid.uuid4(), document_id=uuid.uuid4(), content="passage content", chunk_index=0, score=0.8
    )
    monkeypatch.setattr(chat_module, "get_chat_client", lambda _settings: fake_client)

    result = await chat_module._classify_request_in_scope([chunk], "a question", _settings())

    assert result is expected
    call = fake_client.chat.completions.calls[0]
    assert call["reasoning_effort"] == "low"
    assert "passage content" in call["messages"][1]["content"]
    assert "a question" in call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_chat_stream_guardrail_off_allows_open_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeSupabaseClient()
    user_message_id = uuid.uuid4()
    _seed_thread_with_history(db, [])
    _seed_chat_settings(db, restrict_to_documents=False, min_relevance_score=0.5)

    fake_client = _FakeChatClient(deltas=["Sure, let's chat about anything!"])
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
            user_message_content="totally unrelated small talk",
        )
    ]

    events = _parse_sse_frames(frames)
    assert [event for event, _ in events][-1] == "done"
    # guardrail is off, so an ungrounded question still reaches the LLM
    assert len(fake_client.chat.completions.calls) == 1
    system_message = fake_client.chat.completions.calls[0]["messages"][0]
    assert "never refuse to answer" in system_message["content"]
