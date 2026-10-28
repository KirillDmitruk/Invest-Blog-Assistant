"""Read-only ETF identity diagnostics for Invest Blog Assistant.

Run from project root:
  uv run python tools/inspect_etf_modes.py --suffix a42bcb4d

Loads T-Invest *instrument metadata only* with the project's read-only token.
Never prints tokens, complete broker account/operation IDs, or raw responses.
Does not modify SQLite or call trading endpoints.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3
from pathlib import Path

from invest_blog.config import Settings
from invest_blog.tinvest import TInvest

BUY = {
    "OPERATION_TYPE_BUY", "OPERATION_TYPE_BUY_CARD",
    "OPERATION_TYPE_BUY_MARGIN", "OPERATION_TYPE_DELIVERY_BUY",
    "OPERATION_TYPE_PRIMARY_ORDER",
}
SELL = {
    "OPERATION_TYPE_SELL", "OPERATION_TYPE_SELL_CARD",
    "OPERATION_TYPE_SELL_MARGIN", "OPERATION_TYPE_DELIVERY_SELL",
}


def suffix(s: object) -> str:
    val = str(s or "")
    return f"…{val[-8:]}" if val else "нет"


def q(raw: dict) -> Decimal:
    value = raw.get("quantityDone")
    if value is None:
        value = raw.get("quantity")
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation, TypeError):
        return Decimal(0)
    return result if result.is_finite() and result >= 0 else Decimal(0)


def collect(path: Path, key_suffix: str) -> dict:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = []
        for r in con.execute("SELECT account_id, kind, executed_at, instrument_id, raw_json FROM broker_operations"):
            try:
                raw = json.loads(r["raw_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            uid = str(raw.get("instrumentUid") or r["instrument_id"] or "")
            pos = str(raw.get("positionUid") or "")
            asset = str(raw.get("assetUid") or "")
            if not uid and not pos and not asset:
                continue
            rows.append({
                "account": r["account_id"], "day": str(r["executed_at"])[:10],
                "uid": uid, "position": pos, "asset": asset,
                "kind": r["kind"], "qty": q(raw),
            })
        matched = [r for r in rows if r["uid"].lower().endswith(key_suffix.lower())]
        if not matched:
            return {"matched": [], "related": [], "coverage": {}, "current": {}}
        accounts = {r["account"] for r in matched}
        assets = {r["asset"] for r in matched if r["asset"]}
        related = [r for r in rows if r["account"] in accounts and r["asset"] in assets]
        coverage = {}
        current = {}
        for aid in accounts:
            intervals = con.execute(
                "SELECT first_day,last_day FROM operation_import_ranges WHERE account_id=? ORDER BY first_day",
                (aid,),
            ).fetchall()
            coverage[aid] = [(i[0], i[1]) for i in intervals]
            snap = con.execute(
                "SELECT day FROM portfolio_snapshots WHERE account_id=? ORDER BY day DESC LIMIT 1",
                (aid,),
            ).fetchone()
            current[aid] = snap[0] if snap else "нет"
        return {"matched": matched, "related": related, "coverage": coverage, "current": current}
    finally:
        con.close()


def report(data: dict, api=None) -> str:
    if not data["matched"]:
        return "Инструмент не найден в локальной истории. Ничего не изменено."
    related = [r for r in data["related"] if r["kind"] in BUY | SELL]
    groups = defaultdict(list)
    for r in related:
        groups[(r["account"], r["uid"])].append(r)
    lines = ["Проверка связанной истории ETF (только чтение)"]
    infos = {}
    for (aid, uid), records in sorted(groups.items(), key=lambda pair: min(r["day"] for r in pair[1])):
        buys = sum((r["qty"] for r in records if r["kind"] in BUY), Decimal(0))
        sells = sum((r["qty"] for r in records if r["kind"] in SELL), Decimal(0))
        positions = {r["position"] for r in records if r["position"]}
        asset = next((r["asset"] for r in records if r["asset"]), "")
        fingerprint = hashlib.sha256(str(aid).encode()).hexdigest()[:8]
        lines.extend([
            f"\nСчёт (хеш) {fingerprint}, инструмент {suffix(uid)}",
            f"  positionUid: {', '.join(sorted(map(suffix, positions))) or 'нет'}; assetUid: {suffix(asset)}",
            f"  Операции {min(r['day'] for r in records)} — {max(r['day'] for r in records)}",
            f"  BUY={buys}, SELL={sells}, итог={buys - sells}",
        ])
        if api is not None:
            try:
                instrument = api.instrument(uid=uid)
                raw = instrument.raw
                info = {
                    "isin": str(instrument.isin or ""),
                    "ticker": str(instrument.ticker or ""),
                    "name": str(instrument.name or ""),
                    "class": str(raw.get("classCode") or ""),
                    "exchange": str(raw.get("realExchange") or raw.get("exchange") or ""),
                    "position": str(raw.get("positionUid") or ""),
                    "lot": raw.get("lot"),
                }
                infos[uid] = info
                lines.append(
                    f"  API: {info['name']} | {info['ticker']} | "
                    f"ISIN={info['isin'] or 'нет'} | classCode={info['class'] or 'нет'} | "
                    f"площадка={info['exchange'] or 'нет'} | лот={info['lot'] or 'нет'}"
                )
                lines.append(f"  API positionUid={suffix(info['position'])}")
            except Exception as exc:
                # Never print an exception message that might contain request metadata.
                lines.append(f"  API: метаданные недоступны ({type(exc).__name__})")

    by_account = defaultdict(list)
    for r in related:
        by_account[r["account"]].append(r)
    for aid, records in by_account.items():
        buys = sum((r["qty"] for r in records if r["kind"] in BUY), Decimal(0))
        sells = sum((r["qty"] for r in records if r["kind"] in SELL), Decimal(0))
        lines.extend([
            "\nОбщая арифметика связанных торгов:",
            f"  BUY={buys}; SELL={sells}; нетто={buys-sells}",
            f"  Последний снимок: {data['current'].get(aid, 'нет')}",
            f"  Импортированные интервалы: {', '.join(f'{a}—{b}' for a,b in data['coverage'].get(aid,[])) or 'нет'}",
        ])
        if sells > buys:
            lines.append(
                f"  Для нулевого конечного остатка потребовалось бы {sells-buys} шт. "
                "на начало периода или эквивалентное движение через перевод/событие. "
                "Это проверочная гипотеза, НЕ установленный факт."
            )
    if api is not None:
        isins = {v["isin"] for v in infos.values() if v["isin"]}
        if len(infos) != len({uid for (_, uid) in groups}):
            lines.append("\nНе все инструменты удалось идентифицировать по API: объединение запрещено.")
        elif len(isins) == 1 and all(info["isin"] for info in infos.values()):
            lines.append("\nСовпадает ISIN: вероятно, один выпуск ETF, но это не подтверждает перевод между positionUid.")
        elif len(isins) > 1:
            lines.append("\nISIN РАЗЛИЧАЮТСЯ: не объединяй количества разных инструментов.")
        else:
            lines.append("\nISIN недостаточно для проверки идентичности.")
    lines.append("\nБаза и брокерский счёт не изменены; секреты не выводились.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only ETF instrument identity and cash-flow check")
    parser.add_argument("--suffix", default="a42bcb4d")
    parser.add_argument("--offline", action="store_true", help="Не обращаться к T-Invest API")
    args = parser.parse_args()
    if len(args.suffix) != 8 or not args.suffix.isalnum():
        parser.error("--suffix: ровно 8 латинских букв или цифр")
    settings = Settings.load()
    path = Path(settings.db_path).expanduser()
    if not path.is_file():
        parser.error("SQLite не найдена (проверь DB_PATH)")
    data = collect(path, args.suffix)
    api = None
    if not args.offline and data["matched"]:
        settings.validate_api()
        api = TInvest(settings.invest_token, settings.timezone)
    print(report(data, api))


if __name__ == "__main__":
    main()
