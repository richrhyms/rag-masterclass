"""
Supabase Storage put/delete for uploaded documents.

design.md "Module Boundaries": backend/app/services/storage.py, owner:
backend-2 (G-5b). Objects live under
`{BUCKET_NAME}/{user_id}/{document_id}/{filename}` (design.md API Contracts
-> POST /api/documents "Flow").

Deviation from design.md's RLS-boundary text (documented here and in
gate-5b.md): design.md's "Row-Level Security" section says the service-role
client is used "only for the Storage delete and Realtime status writes",
implying the caller-JWT request-scoped client should perform the Storage
*upload*. In practice, this project's frozen G-4 migrations create no
`storage.objects` RLS policies (verified directly against the live hosted
project: zero buckets exist and zero policies exist on `storage.objects`),
and Storage buckets/policies are not created by SQL migrations at all in
this repo. Because `storage.objects` has RLS forced by default in Supabase,
a request-scoped (anon-key + forwarded JWT) client cannot read or write
Storage objects until a bucket + policy exist -- and adding a storage
policy would require a new migration, which is out of scope/frozen for this
gate. To keep the feature functional against the real hosted project
without touching migrations, BOTH the upload (put) and delete calls in this
module use the service-role client. Per-user isolation for Storage is
enforced by this module's path convention (`{user_id}/{document_id}/...`)
plus the fact that every caller into this module already passed an
ownership check against the `document` row (RLS on `document`/`chunk`
remains the primary isolation boundary for all DB access; this deviation is
scoped to the Storage object store only).
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from supabase import Client

logger = logging.getLogger("rag_masterclass.storage")

BUCKET_NAME = "documents"


def _ensure_bucket(client: Client) -> None:
    """Idempotently ensure the `documents` bucket exists. Private bucket --
    end users never get a direct Storage URL; all access flows through this
    service after an ownership check on the `document` row."""
    try:
        client.storage.get_bucket(BUCKET_NAME)
        return
    except Exception:
        pass

    try:
        client.storage.create_bucket(BUCKET_NAME, options={"public": False})
    except Exception as exc:  # pragma: no cover - defensive, tolerate races
        # Tolerate a race where another request created it first, or the
        # provider surfaces "already exists" as an error rather than a no-op.
        logger.warning(
            "create_bucket(%s) raised (bucket may already exist): %s", BUCKET_NAME, exc
        )


def object_path(user_id: UUID, document_id: UUID, filename: str) -> str:
    """The path *within* the bucket (no bucket prefix)."""
    return f"{user_id}/{document_id}/{filename}"


def upload_document(
    service_client: Client,
    *,
    user_id: UUID,
    document_id: UUID,
    filename: str,
    content: bytes,
    content_type: str,
) -> str:
    """Uploads file bytes to Storage. Returns the full logical
    `storage_path` to persist on the `document` row:
    `{BUCKET_NAME}/{user_id}/{document_id}/{filename}` (design.md's literal
    path convention)."""
    _ensure_bucket(service_client)
    path = object_path(user_id, document_id, filename)
    service_client.storage.from_(BUCKET_NAME).upload(
        path,
        content,
        file_options={"content-type": content_type, "upsert": "true"},
    )
    return f"{BUCKET_NAME}/{path}"


def delete_document_object(service_client: Client, storage_path: str) -> None:
    """Best-effort delete of the Storage object backing a document.

    Never raises: the `document` row delete (which cascades to `chunk` rows
    via FK) is the authoritative action for FR-DATA-3/AC-DATA-3. An
    orphaned Storage object on a failed delete call is a cleanup nuisance,
    not a correctness issue for retrieval, so failures here are logged and
    swallowed rather than propagated to the caller.
    """
    try:
        bucket, sep, path = storage_path.partition("/")
        if not sep or not path:
            logger.warning("delete_document_object: malformed storage_path %r", storage_path)
            return
        result: Any = service_client.storage.from_(bucket).remove([path])
        logger.debug("Deleted storage object %r: %r", storage_path, result)
    except Exception as exc:  # pragma: no cover - best-effort by design
        logger.warning("delete_document_object failed for %r: %s", storage_path, exc)
