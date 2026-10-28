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
            columns = {r[1] for r in db.execute("PRAGMA table_info(drafts)")}
            for column, declaration in {
                "scheduled_at": "TEXT", "published_at": "TEXT", "channel_message_id": "INTEGER",
                "last_error": "TEXT", "revision": "INTEGER NOT NULL DEFAULT 1"
            }.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE drafts ADD COLUMN {column} {declaration}")

            db.execute("CREATE TABLE IF NOT EXISTS editorial_publish_requests (draft_id INTEGER PRIMARY KEY REFERENCES drafts(id), revision INTEGER NOT NULL, requested_at TEXT NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=20000")
        return db

    def insert(self, draft: Draft, *, scheduled: bool) -> tuple[int, bool]:
        key = f"{draft.kind}:{draft.period}" if scheduled else f"manual:{uuid.uuid4().hex}"
        with self._connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO drafts(dedup_key,kind,period,body,state,created_at) VALUES (?,?,?,?,?,?)",
                (key, draft.kind, draft.period, draft.body, 'new', datetime.now(timezone.utc).isoformat()),
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
        if not body.strip():
            raise ValueError('Пустой текст поста')
        with self._connect() as db:
            result = db.execute("UPDATE drafts SET body=?,state='review',scheduled_at=NULL,revision=revision+1 "
                          "WHERE id=? AND state NOT IN ('published','publishing')", (body, draft_id))
            if result.rowcount != 1:
                raise ValueError('Черновик отсутствует либо уже опубликован')
            db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?', (draft_id,))

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

    def change_state(self, draft_id: int, state: str, expected_revision: int | None = None) -> bool:
        """Only permitted workflow transitions; never resurrect a published draft."""
        allowed = {
            'review': ('new', 'draft', 'rejected', 'approved', 'scheduled'),
            'approved': ('review', 'new', 'draft', 'scheduled'),
            'rejected': ('review', 'new', 'draft', 'approved', 'scheduled'),
        }
        if state not in allowed:
            raise ValueError('Недопустимое состояние')
        with self._connect() as db:
            marks = ','.join('?' for _ in allowed[state])
            revision_clause = ' AND revision=?' if expected_revision is not None else ''
            params = (state, draft_id, *allowed[state]) + ((expected_revision,) if expected_revision is not None else ())
            result = db.execute(f'''UPDATE drafts SET state=?, scheduled_at=NULL,revision=revision+1
                WHERE id=? AND state IN ({marks}){revision_clause}''', params)
            if result.rowcount:
                db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?', (draft_id,))
            return result.rowcount == 1

    def schedule(self, draft_id: int, when_utc: str, expected_revision: int | None = None) -> bool:
        with self._connect() as db:
            revision_clause = ' AND revision=?' if expected_revision is not None else ''
            result = db.execute("UPDATE drafts SET state='scheduled', scheduled_at=?,revision=revision+1 WHERE id=? AND state='approved'" + revision_clause,
                                (when_utc, draft_id) + ((expected_revision,) if expected_revision is not None else ()))
            if result.rowcount:
                db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?', (draft_id,))
            return result.rowcount == 1

    def due_scheduled(self, now_utc: str):
        with self._connect() as db:
            return db.execute('''SELECT * FROM drafts WHERE state='scheduled' AND scheduled_at <= ?
                                   ORDER BY scheduled_at LIMIT 10''', (now_utc,)).fetchall()

    def claim_publish(self, draft_id: int, scheduled: bool = False) -> bool:
        """Single winner across concurrent clicks. Ambiguous failure requires manual review."""
        expected = 'scheduled' if scheduled else 'approved'
        with self._connect() as db:
            result = db.execute("UPDATE drafts SET state='publishing',last_error=NULL,revision=revision+1 WHERE id=? AND state=?",
                                (draft_id, expected))
            if result.rowcount:
                db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?', (draft_id,))
            return result.rowcount == 1

    def create_manual(self, kind: str, period: str, body: str) -> int:
        from .model import Draft
        from .editorial import RUBRICS
        if kind not in RUBRICS:
            raise ValueError('Неизвестная рубрика')
        if not isinstance(period, str) or not (1 <= len(period) <= 40):
            raise ValueError('Период обязателен (не более 40 символов)')
        if not isinstance(body, str) or not (1 <= len(body.strip()) <= 10000):
            raise ValueError('Текст должен содержать от 1 до 10000 символов')
        did, _ = self.insert(Draft(kind, period, body.strip()), scheduled=False)
        return did

    def update_body(self, did: int, body: str, revision: int) -> None:
        """Optimistic locking between Telegram and local web editor."""
        if not isinstance(body, str) or not (1 <= len(body.strip()) <= 10000):
            raise ValueError('Текст должен содержать от 1 до 10000 символов')
        with self._connect() as db:
            updated = db.execute("UPDATE drafts SET body=?,state='review',scheduled_at=NULL, revision=revision+1 WHERE id=? AND revision=? AND state NOT IN ('published','publishing')",
                                 (body.strip(), did, revision))
            if not updated.rowcount:
                raise RuntimeError('Черновик изменился с момента открытия или уже опубликован. Обнови страницу.')
            db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?', (did,))

    def queue_publish(self, did: int) -> bool:
        """Web process queues a request; only bot process sends to Telegram."""
        with self._connect() as db:
            row = db.execute('SELECT state,revision,body FROM drafts WHERE id=?', (did,)).fetchone()
            if not row or row['state'] != 'approved' or not (1 <= len(row['body']) <= 4096):
                return False
            db.execute("INSERT INTO editorial_publish_requests(draft_id,revision,requested_at) VALUES(?,?,?) ON CONFLICT(draft_id) DO UPDATE SET revision=excluded.revision, requested_at=excluded.requested_at",
                       (did, row['revision'], datetime.now(timezone.utc).isoformat()))
            return True

    def pending_publications(self, limit: int = 10):
        with self._connect() as db:
            return db.execute("SELECT d.* FROM editorial_publish_requests q JOIN drafts d ON d.id=q.draft_id WHERE d.state='approved' AND d.revision=q.revision ORDER BY q.requested_at LIMIT ?", (limit,)).fetchall()

    def publish_success(self, draft_id: int, message_id: int) -> None:
        with self._connect() as db:
            db.execute('''UPDATE drafts SET state='published',published_at=?,channel_message_id=?,last_error=NULL
                WHERE id=? AND state='publishing' ''',
                (datetime.now(timezone.utc).isoformat(), message_id, draft_id))

    def publish_failure(self, draft_id: int, message: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE drafts SET last_error=? WHERE id=? AND state='publishing'",
                       (message[:500], draft_id))
