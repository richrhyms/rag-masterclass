"""
In-memory fakes for the `supabase.Client` chainable query-builder API, scoped
to the exact call patterns used by `app/routers/threads.py`,
`app/services/chat.py`, and `app/services/retrieval.py`.

Module-local on purpose (not `conftest.py`) per the G-5a mailbox's test
collision-avoidance note -- backend-2 (G-5b) tests do not need to know about
or share this file.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable


class FakeResult:
    def __init__(self, data: list[dict] | dict | None) -> None:
        self.data = data


class _FakeQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows  # shared reference into the table's backing list
        self._filtered = list(rows)
        self._order_key: str | None = None
        self._order_desc = False
        self._mode = "select"
        self._payload: dict | None = None
        self._single = False
        self._limit: int | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def insert(self, payload: dict) -> "_FakeQuery":
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict) -> "_FakeQuery":
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, key: str, value: Any) -> "_FakeQuery":
        self._filtered = [row for row in self._filtered if str(row.get(key)) == str(value)]
        return self

    def order(self, key: str, desc: bool = False) -> "_FakeQuery":
        self._order_key = key
        self._order_desc = desc
        return self

    def limit(self, count: int) -> "_FakeQuery":
        self._limit = count
        return self

    def single(self) -> "_FakeQuery":
        """Mimics postgrest-py's `.single()`: the response's `.data` becomes
        a single dict (not a list) -- the first matching row, or `None` if no
        row matched. Real Postgrest actually errors on 0-or-2+ rows; this
        fake is intentionally lenient (returns `None` on zero rows) since no
        current caller relies on that error behavior."""
        self._single = True
        return self

    def execute(self) -> FakeResult:
        if self._mode == "insert":
            row = dict(self._payload or {})
            row.setdefault("id", str(uuid.uuid4()))
            now = datetime.now(timezone.utc).isoformat()
            row.setdefault("created_at", now)
            row.setdefault("updated_at", now)
            self._rows.append(row)
            return FakeResult([row])

        if self._mode == "update":
            matched_ids = {id(row) for row in self._filtered}
            updated = []
            for row in self._rows:
                if id(row) in matched_ids:
                    row.update(self._payload or {})
                    updated.append(row)
            return FakeResult(updated)

        rows = list(self._filtered)
        if self._order_key:
            rows.sort(key=lambda row: row.get(self._order_key), reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._single:
            return FakeResult(rows[0] if rows else None)
        return FakeResult(rows)


class _FakeRpcQuery:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def execute(self) -> FakeResult:
        return FakeResult(self._rows)


class FakeSupabaseClient:
    """Minimal in-memory stand-in for `supabase.Client`, supporting the
    `.table(name).select/insert/update().eq().order().execute()` and
    `.rpc(name, params).execute()` call shapes used by this gate's code."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.rpc_handler: Callable[[str, dict], list[dict]] | None = None

    def table(self, name: str) -> _FakeQuery:
        self.tables.setdefault(name, [])
        return _FakeQuery(self.tables[name])

    def rpc(self, name: str, params: dict) -> _FakeRpcQuery:
        rows = self.rpc_handler(name, params) if self.rpc_handler else []
        return _FakeRpcQuery(rows)
