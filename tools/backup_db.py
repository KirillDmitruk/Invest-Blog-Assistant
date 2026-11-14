"""Create a consistent SQLite backup without stopping the running bot.

Usage (from the project root):
    uv run python tools/backup_db.py
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(source: Path, destination_dir: Path) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"База данных не найдена: {source}")

    destination_dir = destination_dir.expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    destination = destination_dir / f"{source.stem}-{timestamp}.sqlite3"
    if destination == source:
        raise ValueError("Нельзя сохранять копию поверх исходной базы")

    try:
        # SQLite online backup includes committed data even when WAL is enabled.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as copy:
                original.backup(copy)
                result = copy.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"Проверка резервной копии не пройдена: {result}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Безопасная резервная копия SQLite")
    parser.add_argument("--db", type=Path, help="Путь к базе (по умолчанию DB_PATH из .env)")
    parser.add_argument("--out-dir", type=Path, default=Path("data/backups"),
                        help="Каталог копий (по умолчанию data/backups)")
    args = parser.parse_args()
    if args.db is None:
        from invest_blog.config import Settings
        source = Settings.load().db_path
    else:
        source = args.db
    destination = backup_database(source, args.out_dir)
    print(f"Резервная копия создана и проверена: {destination}")


if __name__ == "__main__":
    main()
