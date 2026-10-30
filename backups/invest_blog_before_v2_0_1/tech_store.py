"""Isolated Tech editorial queue. No automatic publication in v2.0.0."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

class TechDraftStore:
    def __init__(self, path: Path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tech_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                source_url TEXT,
                state TEXT NOT NULL DEFAULT 'draft',
                revision INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )''')
    def _db(self):
        db=sqlite3.connect(self.path, timeout=20)
        db.row_factory=sqlite3.Row
        return db
    def list_recent(self, limit: int=100):
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tech_drafts WHERE state!='deleted' ORDER BY id DESC LIMIT ?",(limit,))]
    def get(self, draft_id: int):
        with self._db() as db:
            row=db.execute('SELECT * FROM tech_drafts WHERE id=? AND state!=\'deleted\'',(draft_id,)).fetchone()
            return dict(row) if row else None
    def create(self, title: str, body: str, source_url: str=''):
        self._validate(title, body, source_url)
        now=datetime.now(timezone.utc).isoformat()
        with self._db() as db:
            result=db.execute('INSERT INTO tech_drafts(title,body,source_url,created_at,updated_at) VALUES (?,?,?,?,?)',(title.strip(),body.strip(),source_url.strip(),now,now))
            return result.lastrowid
    @staticmethod
    def _validate(title, body, source_url):
        if not isinstance(title,str) or not 1<=len(title.strip())<=150: raise ValueError('Заголовок: от 1 до 150 символов')
        if not isinstance(body,str) or not 1<=len(body.strip())<=4000: raise ValueError('Пост: от 1 до 4000 символов')
        if not isinstance(source_url,str) or len(source_url)>2000 or (source_url and not source_url.startswith('https://')): raise ValueError('Источник должен быть HTTPS-ссылкой')
    def update(self, draft_id:int, title:str, body:str, source_url:str, revision:int):
        self._validate(title,body,source_url)
        with self._db() as db:
            cur=db.execute("UPDATE tech_drafts SET title=?,body=?,source_url=?,revision=revision+1,updated_at=? WHERE id=? AND revision=? AND state='draft'",
                (title.strip(),body.strip(),source_url.strip(),datetime.now(timezone.utc).isoformat(),draft_id,revision))
            return cur.rowcount==1
    def delete(self, draft_id:int, revision:int):
        with self._db() as db:
            result=db.execute("UPDATE tech_drafts SET state='deleted',revision=revision+1 WHERE id=? AND revision=? AND state='draft'",(draft_id,revision))
            return result.rowcount==1
