"""Inspect possible duplicate trades in local SQLite without showing private IDs.

Usage: uv run python tools/inspect_trade_duplicates.py --suffix d803883c --day 2026-10-28
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date

from invest_blog.config import Settings
from invest_blog.portfolio_store import PortfolioStore


def short_hash(value: str | None) -> str:
    if not value:
        return 'нет'
    return hashlib.sha256(str(value).encode()).hexdigest()[:10]


def summarize(raw: dict) -> dict:
    trades = raw.get('tradesInfo') or {}
    if isinstance(trades, dict):
        trades = trades.get('trades') or []
    return {
        'operation_id_hash': short_hash(raw.get('id')),
        'cursor_hash': short_hash(raw.get('cursor')),
        'state': raw.get('state'),
        'executed_at': raw.get('date'),
        'quantity_done': raw.get('quantityDone'),
        'quantity': raw.get('quantity'),
        'payment': raw.get('payment'),
        'price': raw.get('price'),
        'trade_nums_hashes': [short_hash(trade.get('num') or trade.get('tradeId'))
                               for trade in trades if isinstance(trade, dict)],
    }


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--suffix', required=True)
    p.add_argument('--day', type=date.fromisoformat, required=True)
    args=p.parse_args()
    store=PortfolioStore(Settings.load().db_path)
    with store.connect() as db:
        rows=db.execute('''SELECT raw_json, account_id FROM broker_operations
            WHERE substr(executed_at,1,10)=? AND instrument_id LIKE ?
            AND kind IN ('OPERATION_TYPE_BUY','OPERATION_TYPE_SELL','OPERATION_TYPE_BUY_CARD','OPERATION_TYPE_SELL_CARD')
            ORDER BY executed_at''',(args.day.isoformat(),'%'+args.suffix)).fetchall()
    print(f'Записей в SQLite: {len(rows)}')
    for row in rows:
        print(json.dumps({'account_hash':short_hash(row['account_id']), **summarize(json.loads(row['raw_json']))},
                         ensure_ascii=False))
    if len(rows)>1:
        print('ВНИМАНИЕ: разные ID могут быть дубликатами старых импортов, но одинаковое время не является доказательством.')

if __name__ == '__main__':
    main()
