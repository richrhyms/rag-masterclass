"""
Metadata field definition endpoints (Module 4, PRD: "Metadata Extraction").

Per-user configurable field definitions -- e.g. "category", "location",
"publish_date" -- that drive what `services/ingestion.py` asks the LLM to
extract from each newly ingested document (see `_default_extract_metadata`).
Deliberately NOT hardcoded per-client field names: a deployment's operator
configures whatever fields matter for their own document set, keeping the
same codebase usable across different client deployments.

RLS (`owner_all`, 20260921000002_metadata_fields.sql) scopes rows to the
caller; no service-role client needed here since this is a plain per-user
table, same pattern as `thread`/`document`.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from supabase import Client

from app.deps import CurrentUser, get_current_user, get_db

router = APIRouter(prefix="/api/metadata-fields", tags=["metadata-fields"])


class MetadataFieldDefinitionOut(BaseModel):
    id: UUID
    name: str
    description: str


class CreateMetadataFieldDefinitionRequest(BaseModel):
    name: str
    description: str


@router.get("", response_model=list[MetadataFieldDefinitionOut])
async def list_metadata_field_definitions(
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> list[MetadataFieldDefinitionOut]:
    response = (
        db.table("metadata_field_definition")
        .select("id,name,description")
        .eq("user_id", str(user.id))
        .order("created_at")
        .execute()
    )
    return [MetadataFieldDefinitionOut(**row) for row in (response.data or [])]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MetadataFieldDefinitionOut)
async def create_metadata_field_definition(
    body: CreateMetadataFieldDefinitionRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> MetadataFieldDefinitionOut:
    name = body.name.strip()
    description = body.description.strip()
    if not name or not description:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Both name and description are required.", "code": "invalid_request"},
        )

    response = (
        db.table("metadata_field_definition")
        .insert({"user_id": str(user.id), "name": name, "description": description})
        .execute()
    )
    return MetadataFieldDefinitionOut(**response.data[0])


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_metadata_field_definition(
    field_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
):
    """Deletes a field definition. Deliberately does NOT retroactively strip
    the field from `document.metadata` on already-ingested documents --
    those extracted values remain as a historical record; only future
    ingestions stop asking the LLM to extract this field. 404 if not
    owned/missing."""
    response = (
        db.table("metadata_field_definition")
        .delete()
        .eq("id", str(field_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    if not response.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Metadata field definition not found.", "code": "not_found"},
        )
    return None
