"""
Chat guardrail settings endpoints (`GET/PATCH /api/settings/chat`).

Workspace-wide singleton (see `supabase/migrations/20260909000001_chat_settings.sql`),
not per-thread/per-user. Controls whether `services/chat.py` restricts answers
to grounded content from the user's own ingested documents, and the minimum
retrieval relevance score required to consider a question "in scope".

Read is RLS-scoped to any authenticated user (any signed-in user can see the
current guardrail state, e.g. to render the Ingestion-view toggle). Write
goes through the service-role client, same pattern as the documented
service-role deviation in `services/storage.py` -- this is a placeholder
until the planned permissions system restricts writes to permitted users
specifically; until then, any authenticated user can flip the toggle, same
as every other endpoint in this app today.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from supabase import Client

from app.deps import CurrentUser, get_current_user, get_db, get_service_client

logger = logging.getLogger("rag_masterclass.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ChatSettingsOut(BaseModel):
    restrict_to_documents: bool
    min_relevance_score: float


class ChatSettingsUpdate(BaseModel):
    restrict_to_documents: bool | None = None
    min_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("/chat", response_model=ChatSettingsOut)
async def get_chat_settings(
    _user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> ChatSettingsOut:
    row = db.table("chat_setting").select("restrict_to_documents,min_relevance_score").eq("id", True).single().execute()
    return ChatSettingsOut(**row.data)


@router.patch("/chat", response_model=ChatSettingsOut)
async def update_chat_settings(
    body: ChatSettingsUpdate,
    _user: CurrentUser = Depends(get_current_user),
    service_client: Client = Depends(get_service_client),
) -> ChatSettingsOut:
    updates = body.model_dump(exclude_none=True)
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        service_client.table("chat_setting").update(updates).eq("id", True).execute()
    row = (
        service_client.table("chat_setting")
        .select("restrict_to_documents,min_relevance_score")
        .eq("id", True)
        .single()
        .execute()
    )
    return ChatSettingsOut(**row.data)
