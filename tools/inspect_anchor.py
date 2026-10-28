"""Read-only local diagnosis of portfolio snapshots versus executed operations.

Usage: uv run python tools/inspect_anchor.py --suffix d803883c
Outputs only suffixes of brokerage account/instrument identifiers. No secrets.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal

from invest_blog.config import Settings
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.history import BUY_KINDS, SELL_KINDS


def dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('В базе обнаружено время без часового пояса')
    return parsed.astimezone(timezone.utc)


def run(suffix: str) -> None:
    settings = Settings.load()
    store = PortfolioStore(settings.db_path)
    with store.connect() as db:
        snapshots = db.execute('''
            SELECT a.account_id, a.day, a.captured_at
            FROM portfolio_snapshots a
            JOIN (SELECT account_id, MAX(day) AS latest_day
                  FROM portfolio_snapshots GROUP BY account_id) b
              ON a.account_id=b.account_id AND a.day=b.latest_day
            ORDER BY a.account_id
        ''').fetchall()
        for snap in snapshots:
            aid, day, captured = snap['account_id'], snap['day'], snap['captured_at']
            print(f'\nСчёт …{aid[-4:]}, снимок за {day}, время {captured}')
            holdings = db.execute('''
                SELECT instrument_id, name, quantity FROM position_snapshots
                WHERE account_id=? AND day=? AND instrument_id LIKE ?
            ''', (aid, day, f'%{suffix}')).fetchall()
            for p in holdings:
                print(f'  Позиция …{p["instrument_id"][-8:]}: {p["name"]} — {p["quantity"]} шт.')
            if not holdings:
                print('  Позиция с таким окончанием UID в снимке не найдена')
            operations = db.execute('''
                SELECT executed_at,kind,instrument_id,raw_json FROM broker_operations
                WHERE account_id=? AND substr(executed_at,1,10)=?
                ORDER BY executed_at
            ''', (aid, day)).fetchall()
            count = 0
            for op in operations:
                if op['kind'] not in BUY_KINDS | SELL_KINDS:
                    continue
                raw = json.loads(op['raw_json'])
                uid = str(op['instrument_id'] or '')
                if suffix and not uid.endswith(suffix):
                    continue
                count += 1
                timing = 'ПОСЛЕ снимка' if dt(op['executed_at']) > dt(captured) else 'до снимка'
                quantity = raw.get('quantityDone') if raw.get('quantityDone') is not None else raw.get('quantity')
                print(f'  {op["executed_at"]} | {op["kind"]} | {quantity} шт. | {timing} | …{uid[-8:]}')
            if not count:
                print('  Нет сделок с таким окончанием UID за день снимка. Проверь FIGI/UID.')
                by_type = {}
                for op in operations:
                    if op['kind'] in BUY_KINDS | SELL_KINDS:
                        by_type[op['kind']] = by_type.get(op['kind'], 0) + 1
                if by_type:
                    print('  Всего сделок в этот день:', by_type)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--suffix', required=True, help='Последние 8 символов UID из ошибки')
    args = parser.parse_args()
    if not 4 <= len(args.suffix) <= 16 or not args.suffix.isalnum():
        parser.error('--suffix должен содержать 4–16 букв или цифр')
    run(args.suffix)
