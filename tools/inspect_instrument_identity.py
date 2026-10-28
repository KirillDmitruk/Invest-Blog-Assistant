"""Read-only identity audit for Invest Blog Assistant (SQLite only).

Usage:
    uv run python tools/inspect_instrument_identity.py --suffix a42bcb4d

Outputs only masked instrument/position/asset/account identifiers. Never outputs
API tokens, complete account IDs, complete broker operation JSON, or op IDs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3

from invest_blog.config import Settings

BUY = {"OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD", "OPERATION_TYPE_BUY_MARGIN", "OPERATION_TYPE_DELIVERY_BUY", "OPERATION_TYPE_PRIMARY_ORDER"}
SELL = {"OPERATION_TYPE_SELL", "OPERATION_TYPE_SELL_CARD", "OPERATION_TYPE_SELL_MARGIN", "OPERATION_TYPE_DELIVERY_SELL"}


def masked(value: object) -> str:
    s = str(value or "")
    return "…" + s[-8:] if s else "нет"


def decimal_or_zero(raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def inspect(db_path: Path, suffix: str) -> str:
    db = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        items = []
        for row in db.execute("SELECT account_id, executed_at, kind, instrument_id, raw_json FROM broker_operations"):
            raw = json.loads(row["raw_json"])
            if not isinstance(raw, dict):
                continue
            identifiers = {k: str(raw.get(k) or "") for k in ("instrumentUid", "positionUid", "assetUid", "figi")}
            instrument = identifiers["instrumentUid"] or str(row["instrument_id"] or "")
            items.append({"account": row["account_id"], "day": row["executed_at"][:10], "kind": row["kind"],
                          "uid": instrument, "position": identifiers["positionUid"],
                          "asset": identifiers["assetUid"], "figi": identifiers["figi"],
                          "amount": decimal_or_zero(raw.get("quantityDone") if raw.get("quantityDone") is not None else raw.get("quantity")),
                          "instrument_type": str(raw.get("instrumentType") or raw.get("instrumentKind") or "нет")})
        matched = [r for r in items if any(s.lower().endswith(suffix.lower()) for s in
                   (r["uid"], r["position"], r["figi"]))]
        if not matched:
            return f"Операции с суффиксом …{suffix} не обнаружены."
        target_accounts = {r["account"] for r in matched}
        assets = {r["asset"] for r in matched if r["asset"]}
        positions = {r["position"] for r in matched if r["position"]}
        related = [r for r in items if r["account"] in target_accounts and (
            r in matched or (r["asset"] and r["asset"] in assets) or
            (r["position"] and r["position"] in positions)
        )]
        groups = defaultdict(list)
        for r in related:
            groups[(r["account"], r["uid"])].append(r)
        lines = [f"Поиск: …{suffix}; прямых операций: {len(matched)}, связанных по assetUid/positionUid: {len(related)}.",
                 "ВНИМАНИЕ: одинаковый assetUid сам по себе НЕ означает одинаковую позицию."]
        all_positions = set()
        all_types = set()
        missing_position = False
        for (account, uid), rows in sorted(groups.items(), key=lambda kv: min(r['day'] for r in kv[1])):
            pos = {r["position"] for r in rows if r["position"]}
            assets_here = {r["asset"] for r in rows if r["asset"]}
            kinds = {r["instrument_type"] for r in rows}
            all_positions.update(pos)
            all_types.update(kinds - {"нет"})
            missing_position |= any(not r["position"] for r in rows if r["kind"] in BUY | SELL)
            bought = sum((r["amount"] for r in rows if r["kind"] in BUY), Decimal(0))
            sold = sum((r["amount"] for r in rows if r["kind"] in SELL), Decimal(0))
            account_fingerprint = hashlib.sha256(account.encode()).hexdigest()[:8]
            lines.append(f"\nТорговый UID {masked(uid)} | счёт (хеш) {account_fingerprint} | {min(r['day'] for r in rows)} — {max(r['day'] for r in rows)}")
            lines.append(f"  positionUid: {', '.join(sorted(map(masked, pos))) if pos else 'нет'}; assetUid: {', '.join(sorted(map(masked, assets_here))) if assets_here else 'нет'}")
            lines.append(f"  instrumentType: {', '.join(sorted(kinds))}")
            lines.append(f"  BUY: {bought}; SELL: {sold}; разница: {bought - sold}; операций: {len(rows)}")
        lines.append("\nПроверка идентичности:")
        if len(all_positions) > 1:
            lines.append("  РАЗНЫЕ positionUid: объединять позиции по assetUid НЕЛЬЗЯ без дополнительного подтверждения.")
        elif len(all_positions) == 1 and not missing_position:
            lines.append("  У всех сделок один и тот же positionUid: вероятно, это одна учитываемая позиция в разных торговых режимах.")
        elif len(all_positions) == 1:
            lines.append("  Есть общий positionUid, но у части сделок он отсутствует: нужно разрешить пропуски через справочник инструментов.")
        else:
            lines.append("  Нет positionUid: данных пока недостаточно для объединения.")
        if len(all_types) > 1:
            lines.append("  Дополнительное предупреждение: разные instrumentType — без проверки объединять нельзя.")
        lines.append("  Скрипт работает только на чтение, изменений SQLite нет.")
        return "\n".join(lines)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix", required=True)
    args = parser.parse_args()
    if len(args.suffix) != 8 or not args.suffix.isalnum():
        parser.error("--suffix должен содержать ровно 8 букв или цифр")
    db_path = Path(Settings.load().db_path).expanduser()
    if not db_path.is_file():
        parser.error("Файл базы не найден. Проверь DB_PATH в .env")
    print(inspect(db_path, args.suffix))


if __name__ == "__main__":
    main()
