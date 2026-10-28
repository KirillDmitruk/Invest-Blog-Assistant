from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .model import Draft


class DraftStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                period TEXT NOT NULL,
                body TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                sent_at TEXT
            );
            CREATE TABLE IF NOT EXISTS telegram_messages (
                message_id INTEGER PRIMARY KEY,
                draft_id INTEGER NOT NULL REFERENCES drafts(id)
            );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        return db

    def insert(self, draft: Draft, *, scheduled: bool) -> tuple[int, bool]:
        key = f"{draft.kind}:{draft.period}" if scheduled else f"manual:{uuid.uuid4().hex}"
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO drafts(dedup_key,kind,period,body,created_at) VALUES (?,?,?,?,?)",
                (key, draft.kind, draft.period, draft.body, datetime.now(timezone.utc).isoformat()),
            )
            row = db.execute("SELECT id FROM drafts WHERE dedup_key=?", (key,)).fetchone()
            return row["id"], cursor.rowcount == 1

    def get(self, draft_id: int) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()

    def list_recent(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT * FROM drafts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def unsent(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT * FROM drafts WHERE sent_at IS NULL ORDER BY id LIMIT 25").fetchall()

    def sent(self, draft_id: int) -> None:
        with self._connect() as db:
            db.execute("UPDATE drafts SET sent_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), draft_id))

    def set_body(self, draft_id: int, body: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE drafts SET body=? WHERE id=?", (body, draft_id))

    def mark_done(self, draft_id: int) -> None:
        with self._connect() as db:
            db.execute("UPDATE drafts SET state='published' WHERE id=?", (draft_id,))

    def link_message(self, msg_id: int, draft_id: int) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO telegram_messages(message_id,draft_id) VALUES (?,?)", (msg_id, draft_id))

    def draft_for_message(self, msg_id: int) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT draft_id FROM telegram_messages WHERE message_id=?", (msg_id,)).fetchone()
            return row["draft_id"] if row else None
