"""
Thread + message + chat (SSE) endpoints (design.md "API Contracts" /
"Module Boundaries": `backend/app/routers/threads.py`, owner: backend-1,
G-5a). Implements exactly:

  GET    /api/threads                       -> { "threads": [...] }
  POST   /api/threads                       -> 201, created thread
  GET    /api/threads/{thread_id}/messages  -> { "messages": [...] }
  POST   /api/threads/{thread_id}/chat      -> text/event-stream (SSE)
  DELETE /api/threads/{thread_id}           -> 204 (post-G-5a addition:
                                                the UI had no way to remove a
                                                conversation; `message.thread_id`
                                                cascades via FK, no extra cleanup
                                                needed)

All four require auth (`Authorization: Bearer <token>`). Errors use the
standard flat envelope `{ "error": string, "code": string }` from design.md's
API Contracts section -- this router returns that shape directly via
`JSONResponse` rather than `HTTPException` (whose default FastAPI handler
wraps `detail` under a `"detail"` key, which would not match the frozen
envelope for the error paths owned here; see the G-5a gate file for a note on
this pre-existing behavior on the `get_current_user`/`get_db` 401 path,
which is out of this router's scope to change).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from supabase import Client

from app.config import Settings, get_settings
from app.deps import CurrentUser, get_current_user, get_db
from app.models import MessageOut, ThreadOut
from app.services.chat import _maybe_set_thread_title, generate_chat_stream

router = APIRouter(prefix="/api/threads", tags=["threads"])


class CreateThreadRequest(BaseModel):
    title: str | None = None


class ChatRequest(BaseModel):
    message: str


def _error(status_code: int, error: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "code": code})


def _not_found() -> JSONResponse:
    return _error(404, "Thread not found.", "not_found")


def _thread_exists(db: Client, user: CurrentUser, thread_id: UUID) -> bool:
    """Ownership check with double scoping (RLS + explicit user_id, NFR-13).
    Under RLS a thread that exists but is owned by another user is
    indistinguishable from a missing thread -- both yield no row, both map to
    404 `not_found`, matching design.md's API contract exactly."""
    response = (
        db.table("thread")
        .select("id")
        .eq("id", str(thread_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    return bool(response.data)


@router.get("")
async def list_threads(
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> dict:
    response = (
        db.table("thread")
        .select("id,title,created_at,updated_at")
        .eq("user_id", str(user.id))
        .order("updated_at", desc=True)
        .execute()
    )
    threads = [ThreadOut(**row) for row in (response.data or [])]
    return {"threads": threads}


@router.post("", status_code=201)
async def create_thread(
    body: CreateThreadRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> ThreadOut:
    response = db.table("thread").insert({"user_id": str(user.id), "title": body.title}).execute()
    return ThreadOut(**response.data[0])


@router.delete("/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Deletes the `thread` row; `message.thread_id` cascades via FK (see
    supabase/migrations/20260818000002_tables.sql) so no separate message
    cleanup is needed. Double-scoped by user_id (NFR-13), same as
    `_thread_exists` above -- a thread owned by another user is
    indistinguishable from a missing one, both 404. The frontend's
    Realtime subscription (ThreadList.tsx) already handles the resulting
    DELETE `postgres_changes` event, so no extra client-side refresh is
    needed beyond issuing this request."""
    response = (
        db.table("thread")
        .delete()
        .eq("id", str(thread_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    if not response.data:
        return _not_found()
    return None


@router.get("/{thread_id}/messages")
async def list_messages(
    thread_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    if not _thread_exists(db, user, thread_id):
        return _not_found()

    response = (
        db.table("message")
        .select("id,role,content,created_at")
        .eq("thread_id", str(thread_id))
        .order("created_at")
        .execute()
    )
    messages = [MessageOut(**row) for row in (response.data or [])]
    return {"messages": messages}


@router.post("/{thread_id}/chat")
async def chat(
    thread_id: UUID,
    body: ChatRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    if not _thread_exists(db, user, thread_id):
        return _not_found()

    message = body.message.strip()
    if not message:
        return _error(400, "message must not be empty.", "invalid_request")

    # Persist the user message before streaming begins (design.md API
    # Contracts / SSE contract).
    insert_response = (
        db.table("message")
        .insert(
            {
                "thread_id": str(thread_id),
                "user_id": str(user.id),
                "role": "user",
                "content": message,
            }
        )
        .execute()
    )
    user_message_id = UUID(insert_response.data[0]["id"])

    db.table("thread").update({"updated_at": datetime.now(timezone.utc).isoformat()}).eq(
        "id", str(thread_id)
    ).execute()

    stream = generate_chat_stream(
        db=db,
        settings=settings,
        user_id=user.id,
        thread_id=thread_id,
        user_message_id=user_message_id,
        user_message_content=message,
    )
    # Thread auto-titling runs as a genuine background task (not inline in
    # the generator) because its latency is observed to vary wildly (see
    # _generate_thread_title's docstring) -- attaching it here means the
    # response closes as soon as the terminal SSE event is flushed,
    # regardless of how long titling ends up taking.
    title_task = BackgroundTask(
        _maybe_set_thread_title, db, settings, thread_id, message
    )
    return StreamingResponse(
        stream,
        background=title_task,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
