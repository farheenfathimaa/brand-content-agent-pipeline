"""SQLite persistence for briefs, state transitions, translations, and CMS docs."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS briefs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    vertical TEXT NOT NULL,
    brief_text TEXT NOT NULL,
    fact_sheet_id TEXT NOT NULL,
    fact_sheet_json TEXT NOT NULL,
    languages_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL,
    headline TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    guardrail_json TEXT,
    initial_guardrail_json TEXT,
    critique_json TEXT NOT NULL DEFAULT '[]',
    revision_count INTEGER NOT NULL DEFAULT 0,
    task_ref_json TEXT,
    design_asset_json TEXT,
    approver TEXT NOT NULL DEFAULT '',
    override_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    brief_id TEXT,
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    status_code INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id TEXT NOT NULL,
    language TEXT NOT NULL,
    text TEXT NOT NULL,
    back_translation TEXT NOT NULL,
    similarity REAL NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cms_documents (
    id TEXT PRIMARY KEY,
    brief_id TEXT NOT NULL,
    language TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'staged', 'published')),
    slug TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_brief ON events(brief_id, id);
CREATE INDEX IF NOT EXISTS idx_translations_brief ON translations(brief_id);
CREATE INDEX IF NOT EXISTS idx_cms_brief ON cms_documents(brief_id);
CREATE INDEX IF NOT EXISTS idx_webhook_brief ON webhook_deliveries(brief_id);
"""

JSON_DEFAULTS: dict[str, Any] = {
    "fact_sheet_json": {},
    "guardrail_json": None,
    "initial_guardrail_json": None,
    "critique_json": [],
    "task_ref_json": None,
    "design_asset_json": None,
    "languages_json": [],
    "metadata_json": {},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def init_schema(self) -> None:
        with self.tx() as connection:
            connection.executescript(SCHEMA)

    # ------------------------------------------------------------------ briefs
    def create_brief(self, record: dict[str, Any], actor: str) -> dict[str, Any]:
        now = utc_now()
        with self.tx() as connection:
            connection.execute(
                """
                INSERT INTO briefs (
                    id, title, vertical, brief_text, fact_sheet_id, fact_sheet_json,
                    languages_json, metadata_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["title"],
                    record["vertical"],
                    record["brief"],
                    record["fact_sheet_id"],
                    json.dumps(record["fact_sheet"], sort_keys=True),
                    json.dumps(record["languages"]),
                    json.dumps(record.get("metadata", {})),
                    record["state"],
                    now,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                INSERT INTO events
                    (brief_id, from_state, to_state, actor, reason, payload_json, created_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["state"],
                    actor,
                    "Brief created",
                    json.dumps({"source": "api"}),
                    now,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
        return self.get_event(event_id)  # type: ignore[return-value]

    def get_brief(self, brief_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM briefs WHERE id = ?", (brief_id,)
            ).fetchone()
        return self._hydrate_brief(row) if row else None

    def list_briefs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM briefs ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._hydrate_brief(row) for row in rows]

    def update_brief_fields(self, brief_id: str, updates: dict[str, Any]) -> None:
        columns: list[str] = []
        values: list[Any] = []
        json_fields = {
            "guardrail_json",
            "initial_guardrail_json",
            "critique_json",
            "task_ref_json",
            "design_asset_json",
        }
        allowed = {
            "headline",
            "body",
            "guardrail_json",
            "initial_guardrail_json",
            "critique_json",
            "revision_count",
            "task_ref_json",
            "design_asset_json",
            "approver",
            "override_reason",
        }
        for key, value in updates.items():
            if key not in allowed:
                raise ValueError(f"Field '{key}' is not updatable.")
            columns.append(f"{key} = ?")
            values.append(json.dumps(value) if key in json_fields else value)
        if not columns:
            return
        columns.append("updated_at = ?")
        values.append(utc_now())
        values.append(brief_id)
        with self.tx() as connection:
            connection.execute(
                f"UPDATE briefs SET {', '.join(columns)} WHERE id = ?", values
            )

    def record_transition(
        self,
        *,
        brief_id: str,
        from_state: str,
        to_state: str,
        actor: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        updates: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically update state + optional content, append an event."""
        now = utc_now()
        allowed = {
            "headline",
            "body",
            "guardrail_json",
            "initial_guardrail_json",
            "critique_json",
            "revision_count",
            "task_ref_json",
            "design_asset_json",
            "approver",
            "override_reason",
        }
        json_fields = {
            "guardrail_json",
            "initial_guardrail_json",
            "critique_json",
            "task_ref_json",
            "design_asset_json",
        }
        updates = updates or {}
        for key in updates:
            if key not in allowed:
                raise ValueError(f"Field '{key}' is not updatable.")

        with self.tx() as connection:
            current = connection.execute(
                "SELECT state FROM briefs WHERE id = ?", (brief_id,)
            ).fetchone()
            if current is None:
                raise KeyError(f"Brief {brief_id} not found.")
            if current["state"] != from_state:
                raise ValueError(
                    f"Concurrent state change for {brief_id}: expected "
                    f"{from_state}, found {current['state']}."
                )
            assignments = ["state = ?", "updated_at = ?"]
            values: list[Any] = [to_state, now]
            for key, value in updates.items():
                assignments.append(f"{key} = ?")
                values.append(json.dumps(value) if key in json_fields else value)
            values.append(brief_id)
            connection.execute(
                f"UPDATE briefs SET {', '.join(assignments)} WHERE id = ?", values
            )
            cursor = connection.execute(
                """
                INSERT INTO events
                    (brief_id, from_state, to_state, actor, reason, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    brief_id,
                    from_state,
                    to_state,
                    actor,
                    reason,
                    json.dumps(payload or {}),
                    now,
                ),
            )
            event_id = int(cursor.lastrowid or 0)
        event = self.get_event(event_id)
        if event is None:  # pragma: no cover - impossible after insert
            raise RuntimeError("Failed to read transition event.")
        return event

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._hydrate_event(row) if row else None

    def list_events(self, brief_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE brief_id = ? ORDER BY id ASC", (brief_id,)
            ).fetchall()
        return [self._hydrate_event(row) for row in rows]

    # -------------------------------------------------------------- webhooks
    def add_webhook_delivery(self, delivery: dict[str, Any]) -> None:
        with self.tx() as connection:
            connection.execute(
                """
                INSERT INTO webhook_deliveries (
                    event_id, brief_id, url, status, signature, timestamp,
                    payload_json, status_code, attempts, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.get("event_id"),
                    delivery.get("brief_id"),
                    delivery.get("url", ""),
                    delivery["status"],
                    delivery.get("signature", ""),
                    delivery.get("timestamp", ""),
                    delivery["payload_json"],
                    delivery.get("status_code"),
                    delivery.get("attempts", 0),
                    delivery.get("error", ""),
                    utc_now(),
                ),
            )

    def list_webhook_deliveries(
        self, brief_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM webhook_deliveries"
        params: tuple[Any, ...] = ()
        if brief_id:
            query += " WHERE brief_id = ?"
            params = (brief_id,)
        query += " ORDER BY id ASC"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._hydrate_webhook(row) for row in rows]

    # ------------------------------------------------------------ translations
    def add_translation(
        self,
        *,
        brief_id: str,
        language: str,
        text: str,
        back_translation: str,
        similarity: float,
        attempt: int,
    ) -> None:
        with self.tx() as connection:
            connection.execute(
                """
                INSERT INTO translations (
                    brief_id, language, text, back_translation, similarity,
                    attempt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    brief_id,
                    language,
                    text,
                    back_translation,
                    similarity,
                    attempt,
                    utc_now(),
                ),
            )

    def list_translations(self, brief_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM translations WHERE brief_id = ? ORDER BY id ASC",
                (brief_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -------------------------------------------------------------------- CMS
    def create_cms_document(self, document: dict[str, Any]) -> None:
        with self.tx() as connection:
            connection.execute(
                """
                INSERT INTO cms_documents (
                    id, brief_id, language, title, body, status, slug,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["id"],
                    document["brief_id"],
                    document["language"],
                    document["title"],
                    document["body"],
                    document["status"],
                    document["slug"],
                    document["created_at"],
                    document["updated_at"],
                ),
            )

    def get_cms_document(self, doc_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM cms_documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_cms_documents(
        self,
        brief_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if brief_id:
            clauses.append("brief_id = ?")
            params.append(brief_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM cms_documents{where} ORDER BY created_at ASC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def set_cms_status(self, brief_id: str, status: str) -> None:
        now = utc_now()
        with self.tx() as connection:
            connection.execute(
                "UPDATE cms_documents SET status = ?, updated_at = ? WHERE brief_id = ?",
                (status, now, brief_id),
            )

    def delete_cms_documents(self, brief_id: str) -> None:
        with self.tx() as connection:
            connection.execute(
                "DELETE FROM cms_documents WHERE brief_id = ?", (brief_id,)
            )

    # ---------------------------------------------------------------- helpers
    def _hydrate_brief(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["brief"] = data.pop("brief_text")
        for column, default in JSON_DEFAULTS.items():
            raw = data.pop(column, None)
            data[column.removesuffix("_json")] = (
                json.loads(raw) if raw not in (None, "") else default
            )
        return data

    def _hydrate_event(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw = data.pop("payload_json", "{}")
        data["payload"] = json.loads(raw) if raw else {}
        return data

    def _hydrate_webhook(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        raw = data.pop("payload_json", "{}")
        data["payload"] = json.loads(raw) if raw else {}
        return data
