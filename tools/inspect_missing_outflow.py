"""Safe local diagnostic for missing sell/redemption/transfer movements.

Run from invest_blog_assistant project root:
    uv run python tools/inspect_missing_outflow.py --suffix a42bcb4d

Only reports masked security/account identifiers, dates, operation TYPES and
quantities. Does not print tokens, account IDs or raw JSON and does not use API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from invest_blog.config import Settings

IDS = ("instrument_id", "instrumentUid", "figi", "positionUid")
MOVEMENT = ("BUY", "SELL", "REPAYMENT", "SECURITIES", "TRANS", "DELIVERY", "PRIMARY", "PLACEMENT", "OTHER")


def _short(value: object) -> str:
    s = str(value or "")
    return "…" + s[-8:] if s else "нет"


def _hash_account(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _identities(row: dict) -> set[str]:
    raw = row["raw"]
    return {str(x) for x in [row["instrument_id"], *(raw.get(k) for k in IDS[1:])] if x}


def _quantity(raw: dict) -> tuple[str, str]:
    done = raw.get("quantityDone")
    ordered = raw.get("quantity")
    return str(done) if done is not None else "нет", str(ordered) if ordered is not None else "нет"


def _as_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def inspect(path: Path, suffix: str, limit: int = 80) -> str:
    if not path.is_file():
        return "База SQLite не найдена. Проверь DB_PATH из .env."
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for r in conn.execute("SELECT account_id,executed_at,kind,instrument_id,raw_json FROM broker_operations ORDER BY executed_at"):
            raw = json.loads(r["raw_json"])
            rows.append({"account_id": r["account_id"], "date": r["executed_at"],
                         "kind": r["kind"], "instrument_id": r["instrument_id"], "raw": raw})
        direct = [r for r in rows if any(x.lower().endswith(suffix.lower()) for x in _identities(r))]
        if not direct:
            return (f"Для …{suffix} нет операций с совпадающим instrumentUid/FIGI/positionUid.\n"
                    "Проверь, что после исправления дедупликации выполнялся sync --days 365.")
        target_accounts = {r["account_id"] for r in direct}
        target_ids = set().union(*(_identities(r) for r in direct))
        target_assets = {str(r["raw"].get("assetUid")) for r in direct if r["raw"].get("assetUid")}
        related = [r for r in rows if r["account_id"] in target_accounts and
                   (_identities(r) & target_ids or
                    (r["raw"].get("assetUid") and str(r["raw"].get("assetUid")) in target_assets))]
        related.sort(key=lambda r: r["date"])
        counts: dict[str, Decimal] = defaultdict(Decimal)
        unknowns: list[str] = []
        output = [f"Инструмент …{suffix}; прямых операций: {len(direct)}; связанных записей: {len(related)}"]
        names = []
        for uid in target_ids:
            try:
                d = conn.execute('SELECT name,ticker FROM instrument_details WHERE instrument_id=?', (uid,)).fetchone()
            except sqlite3.OperationalError:
                d = None
            if d:
                names.append(f"{d['name']} ({d['ticker']})")
        if names:
            output.append("Название: " + "; ".join(sorted(set(names))[:3]))
        for aid in sorted(target_accounts):
            output.append(f"Счёт (хеш): {_hash_account(aid)}")
            snap = conn.execute("SELECT day FROM portfolio_snapshots WHERE account_id=? ORDER BY day DESC LIMIT 1", (aid,)).fetchone()
            if snap:
                positions = conn.execute("SELECT instrument_id,quantity FROM position_snapshots WHERE account_id=? AND day=?", (aid,snap["day"])).fetchall()
                exact = [(p["instrument_id"],p["quantity"]) for p in positions if p["instrument_id"] in target_ids]
                output.append(f"Последний снимок: {snap['day']}, количество по совпадающим ID: " +
                              (", ".join(f"{_short(uid)}={qty}" for uid,qty in exact) if exact else "0 / позиции нет"))
        output.append("Операции (тип, дата, исполнено, заявлено, ID только суффикс):")
        if len(related) > limit:
            output.append(f"Показано последних {limit} из {len(related)}")
        for r in related[-limit:]:
            raw = r["raw"]
            done, ordered = _quantity(raw)
            key = "прямое" if any(x.lower().endswith(suffix.lower()) for x in _identities(r)) else "связано через ID/assetUid"
            output.append(f"  {r['date'][:19]} | {r['kind']} | done={done}, qty={ordered} | {_short(r['instrument_id'])} | {key}")
            kind = r["kind"]
            if "BUY" in kind or "SELL" in kind:
                qty = _as_decimal(raw.get("quantityDone"))
                if qty <= 0:
                    unknowns.append(f"{r['date'][:10]} {kind} — нет положительного quantityDone")
                else:
                    counts["BUY" if "BUY" in kind else "SELL"] += qty
            elif "REPAYMENT_FULL" in kind:
                unknowns.append(f"{r['date'][:10]} полное погашение — количество нужно восстанавливать из события")
            elif any(x in kind for x in MOVEMENT):
                unknowns.append(f"{r['date'][:10]} {kind} — требует отдельной обработки")
        output.append(f"Сумма исполненных BUY={counts['BUY']}, SELL={counts['SELL']} (только для связанных операций)")
        if unknowns:
            output.append("Неоднозначные движения:")
            output.extend("  " + text for text in unknowns[:25])
        # A sell/redemption without an instrument identifier may explain an unmatched purchase.
        unmatched = [r for r in rows if r["account_id"] in target_accounts and
                     not _identities(r) and any(x in r["kind"] for x in MOVEMENT)]
        if unmatched:
            output.append(f"Операций с движением бумаг без ID инструмента: {len(unmatched)}")
            for r in unmatched[-8:]:
                output.append(f"  {r['date'][:10]} {r['kind']} (нужно сопоставлять с отчётом брокера)")
        output.append("Проверяй соответствие с приложением/брокерским отчётом. Ничего в базе не изменено.")
        return "\n".join(output)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Безопасная диагностика движения одной бумаги")
    parser.add_argument("--suffix", required=True, help="Последние 8 символов UID бумаги")
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args()
    if len(args.suffix) != 8 or not args.suffix.isalnum():
        parser.error("--suffix: ровно 8 букв/цифр")
    print(inspect(Path(Settings.load().db_path), args.suffix, args.limit))


if __name__ == "__main__":
    main()
