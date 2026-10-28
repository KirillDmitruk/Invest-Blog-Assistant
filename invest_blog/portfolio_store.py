"""Local historical T-Invest store. No secrets or trade methods are persisted."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .model import money, quotation
from .posts import operation_type


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dec(v) -> str:
    return str(v)


class PortfolioStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                day TEXT NOT NULL, account_id TEXT NOT NULL,
                total_rub TEXT NOT NULL, bonds_rub TEXT NOT NULL,
                shares_rub TEXT NOT NULL, etf_rub TEXT NOT NULL,
                cash_rub TEXT NOT NULL, other_rub TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                PRIMARY KEY(day, account_id)
            );
            CREATE TABLE IF NOT EXISTS position_snapshots (
                day TEXT NOT NULL, account_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
                name TEXT NOT NULL, ticker TEXT NOT NULL, kind TEXT NOT NULL,
                quantity TEXT NOT NULL, price TEXT, currency TEXT,
                nkd TEXT, maturity_date TEXT,
                PRIMARY KEY(day, account_id, instrument_id)
            );
            CREATE TABLE IF NOT EXISTS instrument_details (
                instrument_id TEXT PRIMARY KEY, name TEXT NOT NULL, ticker TEXT NOT NULL,
                maturity_date TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS broker_operations (
                account_id TEXT NOT NULL, op_key TEXT NOT NULL, executed_at TEXT NOT NULL,
                kind TEXT NOT NULL, amount TEXT NOT NULL, currency TEXT NOT NULL,
                instrument_id TEXT, raw_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(account_id, op_key)
            );
            CREATE INDEX IF NOT EXISTS idx_ops_date ON broker_operations(executed_at);
            CREATE TABLE IF NOT EXISTS sync_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
                completed_at TEXT, status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS editorial_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL, topic_key TEXT NOT NULL, draft_id INTEGER,
                created_at TEXT NOT NULL
            );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=30000')
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def existing_detail(self, uid: str):
        with self.connect() as db:
            return db.execute('SELECT * FROM instrument_details WHERE instrument_id=?', (uid,)).fetchone()

    def save_detail(self, uid, name, ticker, maturity):
        with self.connect() as db:
            db.execute('''INSERT INTO instrument_details VALUES (?,?,?,?,?)
            ON CONFLICT(instrument_id) DO UPDATE SET name=excluded.name,
            ticker=excluded.ticker, maturity_date=excluded.maturity_date,
            updated_at=excluded.updated_at''', (uid, name, ticker, maturity, _now()))

    def save_batch(self, day: date, accounts: list[dict], operations: list[tuple[str, dict]], notes: list[str]):
        """All-or-nothing local commit; prevents a half-written multi-account snapshot."""
        with self.connect() as db:
            for account in accounts:
                aid = account['account_id']
                portfolio = account['portfolio']
                values = {}
                for key, field in [('total', 'totalAmountPortfolio'), ('bonds', 'totalAmountBonds'),
                                   ('shares', 'totalAmountShares'), ('etf', 'totalAmountEtf'),
                                   ('cash', 'totalAmountCurrencies'), ('futures', 'totalAmountFutures')]:
                    val, currency = money(portfolio.get(field))
                    if currency != 'RUB' and val:
                        raise ValueError(f'{field} имеет валюту {currency}, ожидается RUB')
                    values[key] = val
                other = values['total'] - sum((values[k] for k in ('bonds','shares','etf','cash')), Decimal(0))
                db.execute('''INSERT INTO portfolio_snapshots VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(day,account_id) DO UPDATE SET total_rub=excluded.total_rub,
                    bonds_rub=excluded.bonds_rub, shares_rub=excluded.shares_rub,
                    etf_rub=excluded.etf_rub, cash_rub=excluded.cash_rub,
                    other_rub=excluded.other_rub, captured_at=excluded.captured_at''',
                    (day.isoformat(), aid, *(_dec(values[k]) for k in ('total','bonds','shares','etf','cash')),
                     _dec(other), _now()))
                db.execute('DELETE FROM position_snapshots WHERE day=? AND account_id=?', (day.isoformat(), aid))
                for pos in account['positions']:
                    db.execute('''INSERT INTO position_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                        (day.isoformat(), aid, pos['id'], pos['name'], pos['ticker'], pos['kind'],
                         pos['quantity'], pos['price'], pos['currency'], pos['nkd'], pos['maturity']))
            for aid, op in operations:
                # API ids can sometimes change; prefer cursor, then ID. A changing cursor
                # can still duplicate a historic operation, so reconcile with broker report.
                base = op.get('cursor') or op.get('id')
                if not base:
                    raise ValueError('Operation without cursor/id: cannot safely deduplicate')
                key = hashlib.sha256(f'{aid}:{base}'.encode()).hexdigest()
                amount, currency = money(op.get('payment'))
                db.execute('''INSERT INTO broker_operations VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account_id,op_key) DO UPDATE SET
                    executed_at=excluded.executed_at, kind=excluded.kind,
                    amount=excluded.amount, currency=excluded.currency,
                    instrument_id=excluded.instrument_id, raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at''',
                    (aid, key, op['date'], operation_type(op), _dec(amount), currency,
                     op.get('instrumentUid') or op.get('figi'), json.dumps(op, ensure_ascii=False), _now()))
            db.execute('INSERT INTO sync_runs(started_at,completed_at,status,notes) VALUES (?,?,?,?)',
                       (_now(), _now(), 'ok' if not notes else 'warning', '\n'.join(notes)))

    def latest_day(self):
        with self.connect() as db:
            row = db.execute('SELECT MAX(day) AS day FROM portfolio_snapshots').fetchone()
        return row['day'] if row else None

    def recent_topics(self, kind: str, limit=12) -> list[str]:
        with self.connect() as db:
            return [r['topic_key'] for r in db.execute('''SELECT topic_key FROM editorial_topics WHERE kind=?
                                    ORDER BY id DESC LIMIT ?''', (kind, limit))]

    def record_topic(self, kind, key, draft_id):
        if not key:
            return
        with self.connect() as db:
            db.execute('INSERT INTO editorial_topics(kind,topic_key,draft_id,created_at) VALUES (?,?,?,?)',
                       (kind, key, draft_id, _now()))

    def get_dashboard_data(self, bond_target: float = .60, max_position: float = .15) -> dict:
        with self.connect() as db:
            rows = db.execute('''SELECT day,
                SUM(CAST(total_rub AS REAL)) AS total,
                SUM(CAST(bonds_rub AS REAL)) AS bonds,
                SUM(CAST(shares_rub AS REAL)) AS shares,
                SUM(CAST(etf_rub AS REAL)) AS etf,
                SUM(CAST(cash_rub AS REAL)) AS cash,
                SUM(CAST(other_rub AS REAL)) AS other
                FROM portfolio_snapshots GROUP BY day ORDER BY day''').fetchall()
            if not rows:
                return {'has_data': False, 'message': 'История отсутствует. Запусти python -m invest_blog.cli sync --days 365.'}
            days = [dict(r) for r in rows]
            last = days[-1]
            pos = [dict(r) for r in db.execute('''SELECT * FROM position_snapshots WHERE day=?
                             ORDER BY kind,name''', (last['day'],))]
            # Group different accounts for identical instrument IDs.
            grouped = {}
            for row in pos:
                ident = row['instrument_id']
                item = grouped.setdefault(ident, {'id': ident, 'name': row['name'], 'ticker': row['ticker'],
                    'kind': row['kind'], 'quantity': 0., 'native_value': 0.,
                    'currency': row['currency'], 'maturity_date': row['maturity_date'], 'valued': True})
                qty = float(row['quantity'])
                item['quantity'] += qty
                if row['price'] is None or row['currency'] != item['currency']:
                    item['valued'] = False
                else:
                    item['native_value'] += float(row['price']) * qty
            positions = list(grouped.values())
            # Denominator is RUB-converted portfolio; a USD price cannot establish RUB share.
            for item in positions:
                item['weight_pct'] = (round(100 * item['native_value']/last['total'], 2)
                   if last['total'] > 0 and item['currency'] == 'RUB' and item['valued'] else None)
            positions.sort(key=lambda p: p['native_value'] if p['currency'] == 'RUB' else -1, reverse=True)
            since = (date.fromisoformat(last['day']) - timedelta(days=365)).isoformat()
            ops = db.execute('''SELECT substr(executed_at,1,7) AS month, kind, currency,
                SUM(CAST(amount AS REAL)) AS amount, COUNT(*) AS n FROM broker_operations
                WHERE substr(executed_at,1,10)>=? GROUP BY month,kind,currency
                ORDER BY month''', (since,)).fetchall()
            sync = db.execute('SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1').fetchone()
            account_days = [dict(r) for r in db.execute('''SELECT account_id,MAX(day) AS last_day
                FROM portfolio_snapshots GROUP BY account_id''')]
        groups = {'bonds':'Облигации','shares':'Акции','etf':'Фонды',
                  'cash':'Денежные средства','other':'Прочее / разница'}
        composition = [{'label':title,'value':round(last[key],2)} for key,title in groups.items() if last[key]>.01]
        inflows = {'OPERATION_TYPE_INPUT', 'OPERATION_TYPE_INPUT_ACQUIRING',
                   'OPERATION_TYPE_INPUT_SWIFT', 'OPERATION_TYPE_INP_MULTI'}
        coupons={'OPERATION_TYPE_COUPON'}
        dividends={'OPERATION_TYPE_DIVIDEND','OPERATION_TYPE_DIV_EXT'}
        monthly = defaultdict(lambda: {'coupons':{}, 'dividends':{}, 'deposits':{}})
        for op in ops:
            category = 'coupons' if op['kind'] in coupons else 'dividends' if op['kind'] in dividends else 'deposits' if op['kind'] in inflows else None
            if category:
                monthly[op['month']][category][op['currency']] = round(
                    monthly[op['month']][category].get(op['currency'],0.) + op['amount'], 2)
        alerts=[]
        if last['other'] < -0.01:
            alerts.append({'level':'attention','text':'Сумма категорий портфеля больше общей оценки. Проверь валютную конвертацию и состав портфеля.'})
        if last['total'] <= 0:
            alerts.append({'level':'info', 'text':'Стоимость портфеля неположительная: доли не рассчитываются.'})
        elif last['bonds']/last['total'] < bond_target:
            alerts.append({'level':'info', 'text': f'Доля облигаций {last["bonds"]/last["total"]*100:.1f}% ниже настроенного ориентира {bond_target*100:.0f}%. Это повод проверить распределение, а не команда купить.'})
        concentrated = [p for p in positions if p['weight_pct'] is not None and p['weight_pct'] > max_position*100]
        for p in concentrated[:5]:
            alerts.append({'level':'attention','text':f'Позиция «{p["name"]}» — ~{p["weight_pct"]:.1f}% портфеля (порог {max_position*100:.0f}%). Проверь риск концентрации.'})
        if any(p['currency'] != 'RUB' or not p['valued'] for p in positions):
            alerts.append({'level':'info','text':'Для части позиций нет рублёвой оценки: их вес и концентрация не определены.'})
        bonds = [p for p in positions if p['kind']=='bond']
        if bonds and not all(p['maturity_date'] for p in bonds):
            alerts.append({'level':'info','text':'Даты погашения известны не для всех облигаций; лестница погашений неполная.'})
        # No credit-rating or issuer classification is inferred from instrument names.
        if bonds:
            alerts.append({'level':'info','text':'Кредитные рейтинги эмитентов не проверяются автоматически. Перед оценкой риска облигаций необходимы внешние рейтинги и отчётность.'})
        ru_coupon_total=sum(x['coupons'].get('RUB',0.) for x in monthly.values())
        ru_dividend_total=sum(x['dividends'].get('RUB',0.) for x in monthly.values())
        if bonds and ru_coupon_total == 0:
            alerts.append({'level':'info','text':'В загруженной истории за год нет рублёвых купонов. Это может означать неполную историю импорта, а не отсутствие выплат.'})
        if last['shares'] > 0 and ru_dividend_total == 0:
            alerts.append({'level':'info','text':'В загруженной истории за год нет рублёвых дивидендов. Проверь полноту истории и цели дивидендной части портфеля.'})
        if len(days)<2:
            alerts.append({'level':'info','text':'История содержит только один день: пока нельзя оценивать динамику.'})
        if any(x['last_day'] != last['day'] for x in account_days):
            alerts.append({'level':'attention','text':'Не все счета имеют снимок за последнюю дату. Сводка может быть неполной.'})
        if sync and sync['status']!='ok':
            alerts.append({'level':'attention','text':'Последняя синхронизация завершилась с предупреждениями: '+sync['notes'][:260]})
        maturity = defaultdict(float)
        for p in bonds:
            if p['currency']=='RUB' and p['valued'] and p['maturity_date']:
                maturity[p['maturity_date'][:4]] += p['native_value']
        if maturity:
            known_sum=sum(maturity.values())
            year, value=max(maturity.items(), key=lambda pair: pair[1])
            if known_sum>0 and value/known_sum>=.50 and len(maturity)>1:
                alerts.append({'level':'attention', 'text':f'По известным рублёвым датам на {year} год приходится ~{value/known_sum*100:.0f}% оценённой стоимости облигаций с известным погашением. Проверь распределение сроков.'})
        return {'has_data':True, 'as_of':last['day'], 'total_rub':round(last['total'],2),
                'rub_income_12m':{'coupons':round(ru_coupon_total,2), 'dividends':round(ru_dividend_total,2)},
                'history':[{'day':r['day'],'total':round(r['total'],2)} for r in days],
                'composition':composition, 'positions':positions,
                'income':[{'month':month,**values} for month,values in sorted(monthly.items())],
                'maturity':[{'year':y,'estimated_value_rub':round(v,2)} for y,v in sorted(maturity.items())],
                'alerts':alerts, 'sync':dict(sync) if sync else None,
                'caveats':['График — стоимость портфеля, не доходность.',
                           'Выплаты агрегированы по валютам без искусственной конвертации.',
                           'Стоимость позиции приблизительная: НКД и переоценка могут отличаться от суммы брокера.',
                           'Вес позиции доступен лишь для бумаг с рублёвой ценой.',
                           'Отсутствующие данные не заменяются нулями.']}
