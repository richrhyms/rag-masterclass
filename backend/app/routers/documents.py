"""
Document upload, list, and delete endpoints (design.md "API Contracts":
POST/GET/DELETE /api/documents, owner: backend-2, G-5b).

Client usage per design.md's RLS-boundary section:
- `get_db` (request-scoped, caller-JWT, RLS-active) is used for the
  document-row insert and for the user-facing list/delete reads/writes.
- `get_service_client` is used for Storage put/delete (see
  app/services/storage.py's module docstring for why upload also uses it,
  a documented deviation) and is handed to the background ingestion
  pipeline (app/services/ingestion.py) for its status/chunk writes.
"""
from __future__ import annotations

import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from supabase import Client

from app.config import Settings, get_settings
from app.deps import CurrentUser, get_current_user, get_db, get_service_client
from app.models import DocumentOut
from app.services import ingestion, storage
from app.services.ingestion import DocumentUploadOut, DocumentsListResponse

logger = logging.getLogger("rag_masterclass.documents")

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Accepted input formats (design.md "Accepted input formats" -- Module 5
# multi-format parsing is out of scope; plain-text-extractable only).
_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    "txt": "text/plain",
    "md": "text/markdown",
}


def _extension_of(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


@router.post("", response_model=DocumentUploadOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
    service_client: Client = Depends(get_service_client),
    settings: Settings = Depends(get_settings),
) -> DocumentUploadOut:
    """multipart upload, one file per request (design.md: "the UI issues one
    request per file for multi-file selection"). Flow: validate ->
    Storage put -> insert `document` row (status='queued') -> kick off the
    async ingestion pipeline -> 202 Accepted."""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "A file is required.", "code": "invalid_request"},
        )

    extension = _extension_of(file.filename)
    content_type = _EXTENSION_CONTENT_TYPES.get(extension)
    if content_type is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error": f"Unsupported file type: '.{extension}'. Only .txt and .md are accepted.",
                "code": "unsupported_file_type",
            },
        )

    # Read up to MAX_UPLOAD_BYTES + 1 so an oversized file can be rejected
    # without buffering an arbitrarily large payload into memory.
    raw = await file.read(settings.MAX_UPLOAD_BYTES + 1)
    if len(raw) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": f"File exceeds the maximum upload size of {settings.MAX_UPLOAD_BYTES} bytes.",
                "code": "file_too_large",
            },
        )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Uploaded file is empty.", "code": "invalid_request"},
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": f"File is not valid UTF-8: {exc}", "code": "invalid_request"},
        ) from exc

    document_id = uuid4()
    byte_size = len(raw)

    try:
        storage_path = storage.upload_document(
            service_client,
            user_id=user.id,
            document_id=document_id,
            filename=file.filename,
            content=raw,
            content_type=content_type,
        )
    except Exception as exc:
        logger.exception("Storage upload failed for document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to store the uploaded file.", "code": "storage_error"},
        ) from exc

    insert_payload = {
        "id": str(document_id),
        "user_id": str(user.id),
        "filename": file.filename,
        "storage_path": storage_path,
        "content_type": content_type,
        "byte_size": byte_size,
        "status": "queued",
    }
    try:
        resp = db.table("document").insert(insert_payload).execute()
        row = resp.data[0]
    except Exception as exc:
        logger.exception("document row insert failed for %s", document_id)
        storage.delete_document_object(service_client, storage_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Failed to create the document record.", "code": "server_error"},
        ) from exc

    background_tasks.add_task(
        ingestion.run_ingestion_pipeline,
        service_client=service_client,
        document_id=document_id,
        user_id=user.id,
        text=text,
        settings=settings,
    )

    return DocumentUploadOut(
        id=document_id,
        filename=row["filename"],
        status=row["status"],
        content_type=row["content_type"],
        byte_size=row["byte_size"],
        created_at=row["created_at"],
    )


@router.get("", response_model=DocumentsListResponse)
async def list_documents(
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
) -> DocumentsListResponse:
    """Ordered by created_at desc (design.md). RLS already scopes rows to
    the caller; the explicit `.eq("user_id", ...)` is defense-in-depth
    matching the "scoped twice" pattern used elsewhere in the design
    (NFR-13)."""
    resp = (
        db.table("document")
        .select("*")
        .eq("user_id", str(user.id))
        .order("created_at", desc=True)
        .execute()
    )
    return DocumentsListResponse(documents=[DocumentOut(**row) for row in resp.data])


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_document(
    document_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_db),
    service_client: Client = Depends(get_service_client),
) -> None:
    """Deletes the `document` row (chunks cascade via FK, FR-DATA-3) and
    best-effort deletes the Storage object. 404 if not owned/missing."""
    resp = (
        db.table("document")
        .delete()
        .eq("id", str(document_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    if not resp.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "Document not found.", "code": "not_found"},
        )

    storage_path = resp.data[0].get("storage_path")
    if storage_path:
        storage.delete_document_object(service_client, storage_path)
    return None
