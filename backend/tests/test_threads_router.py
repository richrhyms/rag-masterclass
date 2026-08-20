import json
import uuid

import pytest
from fastapi.testclient import TestClient

import app.routers.threads as threads_module
from app.deps import CurrentUser, get_current_user, get_db
from app.main import app
from tests.fakes import FakeSupabaseClient

USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


def _current_user() -> CurrentUser:
    return CurrentUser(id=USER_ID, email="user@example.com", claims={})


@pytest.fixture()
def fake_db() -> FakeSupabaseClient:
    return FakeSupabaseClient()


@pytest.fixture()
def client(fake_db: FakeSupabaseClient) -> TestClient:
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_db] = lambda: fake_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


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
