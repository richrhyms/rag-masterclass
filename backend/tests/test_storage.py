import uuid

from app.services import storage
from tests.ingestion_fakes import FakeSupabaseClient


def test_object_path_format() -> None:
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()
    assert storage.object_path(user_id, document_id, "notes.txt") == (
        f"{user_id}/{document_id}/notes.txt"
    )


def test_upload_document_creates_bucket_when_missing_and_stores_content() -> None:
    client = FakeSupabaseClient()
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

    storage_path = storage.upload_document(
        client,
        user_id=user_id,
        document_id=document_id,
        filename="notes.txt",
        content=b"hello world",
        content_type="text/plain",
    )

    assert storage_path == f"{storage.BUCKET_NAME}/{user_id}/{document_id}/notes.txt"
    assert client.storage.create_bucket_calls == [storage.BUCKET_NAME]
    assert client.storage.buckets[storage.BUCKET_NAME][f"{user_id}/{document_id}/notes.txt"] == (
        b"hello world"
    )


def test_upload_document_tolerates_bucket_already_existing() -> None:
    client = FakeSupabaseClient()
    client.storage.buckets[storage.BUCKET_NAME] = {}  # pre-exists
    user_id = uuid.uuid4()
    document_id = uuid.uuid4()

    storage.upload_document(
        client,
        user_id=user_id,
        document_id=document_id,
        filename="a.md",
        content=b"# hi",
        content_type="text/markdown",
    )

    assert client.storage.get_bucket_calls == [storage.BUCKET_NAME]
    assert client.storage.create_bucket_calls == []  # never needed


def test_delete_document_object_removes_stored_object() -> None:
    client = FakeSupabaseClient()
    client.storage.buckets[storage.BUCKET_NAME] = {"u1/d1/f.txt": b"content"}

    storage.delete_document_object(client, f"{storage.BUCKET_NAME}/u1/d1/f.txt")

    assert "u1/d1/f.txt" not in client.storage.buckets[storage.BUCKET_NAME]


def test_delete_document_object_swallows_malformed_path() -> None:
    client = FakeSupabaseClient()
    # No "/" separator at all -- should log and return, never raise.
    storage.delete_document_object(client, "malformed-path-no-bucket")


def test_delete_document_object_swallows_backend_errors() -> None:
    class _ExplodingBucket:
        def remove(self, paths: list[str]) -> None:
            raise RuntimeError("storage backend unavailable")

    class _ExplodingStorage:
        def from_(self, _bucket: str) -> "_ExplodingBucket":
            return _ExplodingBucket()

    class _ExplodingClient:
        storage = _ExplodingStorage()

    # Must not raise -- best-effort by design (the document row delete is
    # the authoritative action; an orphaned Storage object is a cleanup
    # nuisance, not a correctness issue).
    storage.delete_document_object(_ExplodingClient(), "documents/u1/d1/f.txt")
