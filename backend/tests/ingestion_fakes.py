"""
In-memory fakes for the `supabase.Client` API surface used by
`app/routers/documents.py`, `app/services/ingestion.py`, and
`app/services/storage.py` (backend-2, G-5b).

Deliberately a separate, module-local-ish file from `tests/fakes.py`, which
is owned by backend-1's threads/chat/retrieval tests -- per the G-5a
mailbox's test-collision-avoidance note ("prefer module-local fixtures ...
to shrink the shared surface"), backend-2's tests bring their own fake here
rather than extending the shared one, so the two PRs' test suites never need
to coordinate on a single fake's call-shape contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class FakeResult:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeTableQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows  # shared reference into the table's backing list
        self._filtered = list(rows)
        self._order_key: str | None = None
        self._order_desc = False
        self._mode = "select"
        self._insert_payloads: list[dict] | None = None
        self._update_payload: dict | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "_FakeTableQuery":
        return self

    def insert(self, payload: Any) -> "_FakeTableQuery":
        self._mode = "insert"
        self._insert_payloads = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload: dict) -> "_FakeTableQuery":
        self._mode = "update"
        self._update_payload = payload
        return self

    def delete(self) -> "_FakeTableQuery":
        self._mode = "delete"
        return self

    def eq(self, key: str, value: Any) -> "_FakeTableQuery":
        self._filtered = [row for row in self._filtered if str(row.get(key)) == str(value)]
        return self

    def order(self, key: str, desc: bool = False) -> "_FakeTableQuery":
        self._order_key = key
        self._order_desc = desc
        return self

    def execute(self) -> FakeResult:
        if self._mode == "insert":
            inserted = []
            now = datetime.now(timezone.utc).isoformat()
            for payload in self._insert_payloads or []:
                row = dict(payload)
                row.setdefault("id", str(uuid.uuid4()))
                row.setdefault("created_at", now)
                row.setdefault("updated_at", now)
                row.setdefault("chunk_count", 0)
                row.setdefault("error", None)
                self._rows.append(row)
                inserted.append(row)
            return FakeResult(inserted)

        if self._mode == "update":
            matched_ids = {id(row) for row in self._filtered}
            updated = []
            for row in self._rows:
                if id(row) in matched_ids:
                    row.update(self._update_payload or {})
                    updated.append(row)
            return FakeResult(updated)

        if self._mode == "delete":
            matched_ids = {id(row) for row in self._filtered}
            removed = [row for row in self._rows if id(row) in matched_ids]
            self._rows[:] = [row for row in self._rows if id(row) not in matched_ids]
            return FakeResult(removed)

        rows = list(self._filtered)
        if self._order_key:
            rows.sort(key=lambda row: row.get(self._order_key), reverse=self._order_desc)
        return FakeResult(rows)


class FakeStorageBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def upload(self, path: str, content: bytes, file_options: dict | None = None) -> Any:
        self._store[path] = content
        return {"path": path}

    def remove(self, paths: list[str]) -> Any:
        for path in paths:
            self._store.pop(path, None)
        return [{"name": p} for p in paths]


class FakeStorage:
    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, bytes]] = {}
        self.get_bucket_calls: list[str] = []
        self.create_bucket_calls: list[str] = []

    def get_bucket(self, name: str) -> Any:
        self.get_bucket_calls.append(name)
        if name not in self.buckets:
            raise Exception(f"bucket {name!r} not found")
        return {"name": name}

    def create_bucket(self, name: str, options: dict | None = None) -> Any:
        self.create_bucket_calls.append(name)
        self.buckets.setdefault(name, {})
        return {"name": name}

    def from_(self, bucket: str) -> FakeStorageBucket:
        self.buckets.setdefault(bucket, {})
        return FakeStorageBucket(self.buckets[bucket])


class FakeSupabaseClient:
    """Minimal in-memory stand-in for `supabase.Client`, covering the
    `.table(name).insert/select/update/delete().eq().order().execute()` and
    `.storage.from_(bucket).upload/remove(...)` call shapes used by
    documents.py / ingestion.py / storage.py. Supports both single-dict and
    list-of-dicts `.insert(...)` payloads (the `chunk` table insert is a
    batch insert of multiple rows in one call)."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.storage = FakeStorage()

    def table(self, name: str) -> _FakeTableQuery:
        self.tables.setdefault(name, [])
        return _FakeTableQuery(self.tables[name])
