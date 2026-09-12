import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.routers.threads as threads_module
import app.services.chat as chat_module
from app.config import Settings, get_settings
from app.deps import CurrentUser, get_current_user, get_db
from app.main import app
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


def _current_user() -> CurrentUser:
    return CurrentUser(id=USER_ID, email="user@example.com", claims={})


def _override_settings(**overrides) -> Settings:
    # `_env_file=None` is load-bearing, not incidental: without it,
    # pydantic-settings would still pick up the real backend/.env file's
    # LLM_BASE_URL/LLM_API_KEY (real Gemini credentials) even though we
    # never pass them here explicitly -- defeating the whole point of this
    # override, which is to guarantee no test in this file can ever reach
    # a real network call.
    base = dict(_env_file=None)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def fake_db() -> FakeSupabaseClient:
    return FakeSupabaseClient()


@pytest.fixture()
def client(fake_db: FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_db] = lambda: fake_db
    # Without this, get_settings() falls through to the REAL backend/.env --
    # meaning any test that reaches a real LLM client construction (e.g. the
    # /chat endpoint's title-generation background task, unless a given test
    # also mocks _generate_thread_title) would make a real, slow, costly
    # network call. Fake-but-valid settings here make that impossible
    # regardless of what any individual test does or forgets to mock.
    app.dependency_overrides[get_settings] = lambda: _override_settings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_settings, None)


def test_create_and_list_threads_ordered_by_updated_at_desc(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    resp1 = client.post("/api/threads", json={"title": "first"})
    assert resp1.status_code == 201
    thread1 = resp1.json()
    assert thread1["title"] == "first"

    resp2 = client.post("/api/threads", json={"title": None})
    assert resp2.status_code == 201
    thread2 = resp2.json()
    assert thread2["title"] is None

    # bump thread1's updated_at so it should now sort first
    fake_db.tables["thread"][0]["updated_at"] = "2999-01-01T00:00:00+00:00"

    listed = client.get("/api/threads")
    assert listed.status_code == 200
    ids = [t["id"] for t in listed.json()["threads"]]
    assert ids[0] == thread1["id"]
    assert set(ids) == {thread1["id"], thread2["id"]}


def test_list_threads_only_returns_current_users_threads(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    fake_db.tables["thread"] = [
        {
            "id": str(uuid.uuid4()),
            "user_id": str(OTHER_USER_ID),
            "title": "not mine",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    resp = client.get("/api/threads")
    assert resp.status_code == 200
    assert resp.json()["threads"] == []


def test_delete_thread_removes_row(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": "to delete",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    resp = client.delete(f"/api/threads/{thread_id}")

    assert resp.status_code == 204
    assert fake_db.tables["thread"] == []


def test_delete_thread_not_found_returns_404(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    resp = client.delete(f"/api/threads/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_delete_thread_owned_by_another_user_returns_404(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(OTHER_USER_ID),
            "title": "not mine",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    resp = client.delete(f"/api/threads/{thread_id}")

    assert resp.status_code == 404
    # not actually deleted -- ownership check failed before the delete ran
    assert fake_db.tables["thread"] == [
        {
            "id": str(thread_id),
            "user_id": str(OTHER_USER_ID),
            "title": "not mine",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]


def test_list_messages_404_when_thread_not_owned_or_missing(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    other_thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(other_thread_id),
            "user_id": str(OTHER_USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    resp = client.get(f"/api/threads/{other_thread_id}/messages")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Thread not found.", "code": "not_found"}

    missing_id = uuid.uuid4()
    resp2 = client.get(f"/api/threads/{missing_id}/messages")
    assert resp2.status_code == 404


def test_list_messages_ordered_ascending(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    fake_db.tables["message"] = [
        {
            "id": str(uuid.uuid4()),
            "thread_id": str(thread_id),
            "role": "assistant",
            "content": "second",
            "created_at": "2026-01-01T00:01:00+00:00",
        },
        {
            "id": str(uuid.uuid4()),
            "thread_id": str(thread_id),
            "role": "user",
            "content": "first",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
    ]
    resp = client.get(f"/api/threads/{thread_id}/messages")
    assert resp.status_code == 200
    contents = [m["content"] for m in resp.json()["messages"]]
    assert contents == ["first", "second"]


def test_chat_404_when_thread_not_found(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    resp = client.post(f"/api/threads/{uuid.uuid4()}/chat", json={"message": "hello"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "not_found"


def test_chat_400_when_message_empty(client: TestClient, fake_db: FakeSupabaseClient) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    resp = client.post(f"/api/threads/{thread_id}/chat", json={"message": "   "})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_request"
    # no user message persisted on a pre-stream validation failure
    assert fake_db.tables.get("message", []) == []


def test_chat_persists_user_message_and_streams_sse(
    client: TestClient, fake_db: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    async def _fake_stream(**kwargs):
        yield f"event: start\ndata: {json.dumps({'thread_id': str(kwargs['thread_id']), 'user_message_id': str(kwargs['user_message_id'])})}\n\n"
        yield "event: token\ndata: {\"delta\": \"hi\"}\n\n"
        yield "event: done\ndata: {\"assistant_message_id\": \"" + str(uuid.uuid4()) + "\", \"content\": \"hi\"}\n\n"

    monkeypatch.setattr(threads_module, "generate_chat_stream", _fake_stream)

    resp = client.post(f"/api/threads/{thread_id}/chat", json={"message": "hello there"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: start" in resp.text
    assert "event: token" in resp.text
    assert "event: done" in resp.text

    # user message persisted before streaming (per the SSE contract)
    user_messages = [m for m in fake_db.tables["message"] if m["role"] == "user"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == "hello there"


# --- Thread auto-titling (background task attached to the SSE response) ---
#
# `_maybe_set_thread_title` runs as a genuine `starlette.background.BackgroundTask`
# on the `/chat` endpoint's `StreamingResponse` (see chat.py's docstring for why:
# the title-generation LLM call's latency is empirically wildly variable, and
# running it inline inside the SSE stream would keep the HTTP response --
# and therefore the frontend's "sending..." state -- open for that whole
# window even though the visible answer had already streamed in). These
# tests exercise the real `_maybe_set_thread_title`, stubbing only
# `_generate_thread_title` (the actual LLM call) so they stay fast and
# provider-free, matching the pattern used throughout the ingestion/chat
# test suites. `TestClient` runs `response.background` synchronously as
# part of the same request/response cycle, so these can assert on
# `fake_db` immediately after `client.post(...)` returns.


def test_chat_sets_thread_title_on_first_turn(
    client: TestClient, fake_db: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    async def _fake_stream(**kwargs):
        yield f"event: start\ndata: {json.dumps({'thread_id': str(kwargs['thread_id']), 'user_message_id': str(kwargs['user_message_id'])})}\n\n"
        yield "event: done\ndata: {\"assistant_message_id\": \"" + str(uuid.uuid4()) + "\", \"content\": \"hi\"}\n\n"

    monkeypatch.setattr(threads_module, "generate_chat_stream", _fake_stream)

    async def _fake_generate_title(_user_message: str, _settings) -> str:
        return "Solar Battery Types"

    monkeypatch.setattr(chat_module, "_generate_thread_title", _fake_generate_title)

    resp = client.post(f"/api/threads/{thread_id}/chat", json={"message": "what are the best batteries?"})
    assert resp.status_code == 200

    [thread_row] = fake_db.tables["thread"]
    assert thread_row["title"] == "Solar Battery Types"


def test_chat_does_not_retitle_on_later_turns(
    client: TestClient, fake_db: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": "Already Titled",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    # A prior exchange already exists in this thread.
    fake_db.tables["message"] = [
        {
            "id": str(uuid.uuid4()),
            "thread_id": str(thread_id),
            "user_id": str(USER_ID),
            "role": "user",
            "content": "earlier question",
        }
    ]

    async def _fake_stream(**kwargs):
        yield f"event: start\ndata: {json.dumps({'thread_id': str(kwargs['thread_id']), 'user_message_id': str(kwargs['user_message_id'])})}\n\n"
        yield "event: done\ndata: {\"assistant_message_id\": \"" + str(uuid.uuid4()) + "\", \"content\": \"hi\"}\n\n"

    monkeypatch.setattr(threads_module, "generate_chat_stream", _fake_stream)

    generate_title_calls: list[str] = []

    async def _fake_generate_title(user_message: str, _settings) -> str:
        generate_title_calls.append(user_message)
        return "Should Not Be Used"

    monkeypatch.setattr(chat_module, "_generate_thread_title", _fake_generate_title)

    resp = client.post(f"/api/threads/{thread_id}/chat", json={"message": "a follow-up question"})
    assert resp.status_code == 200

    [thread_row] = fake_db.tables["thread"]
    assert thread_row["title"] == "Already Titled"  # untouched
    assert generate_title_calls == []  # title generation never even attempted


def test_chat_title_generation_failure_does_not_affect_chat_response(
    client: TestClient, fake_db: FakeSupabaseClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = uuid.uuid4()
    fake_db.tables["thread"] = [
        {
            "id": str(thread_id),
            "user_id": str(USER_ID),
            "title": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    async def _fake_stream(**kwargs):
        yield f"event: start\ndata: {json.dumps({'thread_id': str(kwargs['thread_id']), 'user_message_id': str(kwargs['user_message_id'])})}\n\n"
        yield "event: done\ndata: {\"assistant_message_id\": \"" + str(uuid.uuid4()) + "\", \"content\": \"hi\"}\n\n"

    monkeypatch.setattr(threads_module, "generate_chat_stream", _fake_stream)

    async def _boom_generate_title(_user_message: str, _settings) -> str:
        raise RuntimeError("title provider unreachable")

    monkeypatch.setattr(chat_module, "_generate_thread_title", _boom_generate_title)

    resp = client.post(f"/api/threads/{thread_id}/chat", json={"message": "hello"})
    assert resp.status_code == 200  # the chat response itself is unaffected
    assert "event: done" in resp.text

    [thread_row] = fake_db.tables["thread"]
    assert thread_row["title"] is None  # left untouched, not crashed
