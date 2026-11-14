"""SQLite online backup must be consistent and non-destructive."""
import sqlite3

import pytest

from tools.backup_db import backup_database


def test_online_backup_preserves_data_and_source(tmp_path):
    source = tmp_path / "drafts.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, body TEXT)")
        db.execute("INSERT INTO posts(body) VALUES ('Не потерять черновик')")

    destination = backup_database(source, tmp_path / "copies")
    assert destination != source
    assert destination.exists()
    with sqlite3.connect(destination) as db:
        assert db.execute("SELECT body FROM posts").fetchone()[0] == "Не потерять черновик"
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 1


def test_backup_rejects_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_database(tmp_path / "missing.sqlite3", tmp_path / "copies")
    assert not (tmp_path / "copies").exists()
