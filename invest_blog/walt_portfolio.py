"""Manual Walt holdings and honest consolidated valuation; no wallet credentials."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo


KINDS = {'crypto': 'Криптовалюта', 'stablecoin': 'Стейблкоины',
         'tokenized_stock': 'Токенизированные акции', 'tokenized_etf': 'Токенизированные фонды',
         'other': 'Другие активы Walt'}


def now():
    return datetime.now(ZoneInfo('Europe/Moscow'))


def amount(value):
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 80
        return format(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP), 'f')


class Conflict(ValueError):
    pass


class WaltPortfolio:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS walt_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                    name TEXT NOT NULL, kind TEXT NOT NULL, quantity TEXT NOT NULL,
                    unit_price_usd TEXT, cost_usd TEXT, valued_on TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                    deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS walt_active_symbol
                    ON walt_positions(lower(symbol)) WHERE deleted=0;
                CREATE TABLE IF NOT EXISTS asset_fx (
                    id INTEGER PRIMARY KEY CHECK(id=1), usd_rub TEXT,
                    as_of TEXT, source TEXT, revision INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO asset_fx(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS asset_valuation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,
                    event TEXT NOT NULL, payload TEXT NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, fields, position_id=None, revision=None, account_ids=()):
        fields = dict(fields)
        for key in ('quantity', 'unit_price_usd', 'cost_usd'):
            fields[key] = format(Decimal(str(fields[key])), 'f') if fields.get(key) is not None else None
        fields['valued_on'] = str(fields['valued_on'])
        values = tuple(fields[key] for key in ('symbol', 'name', 'kind', 'quantity',
                                               'unit_price_usd', 'cost_usd', 'valued_on', 'notes'))
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if position_id is None:
                    cursor = db.execute('''INSERT INTO walt_positions
                        (symbol,name,kind,quantity,unit_price_usd,cost_usd,valued_on,notes,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?)''', (*values, now().isoformat()))
                    position_id = cursor.lastrowid
                else:
                    result = db.execute('''UPDATE walt_positions SET symbol=?,name=?,kind=?,quantity=?,
                        unit_price_usd=?,cost_usd=?,valued_on=?,notes=?,updated_at=?,revision=revision+1
                        WHERE id=? AND revision=? AND deleted=0''', (*values, now().isoformat(), position_id, revision))
                    if result.rowcount != 1:
                        raise Conflict('Позиция изменена или удалена. Обнови список.')
                self._capture(db, 'position_saved', account_ids)
                return dict(db.execute('SELECT * FROM walt_positions WHERE id=?', (position_id,)).fetchone())
        except sqlite3.IntegrityError as exc:
            raise Conflict('Такой тикер уже есть в Walt. Отредактируй существующую позицию.') from exc

    def delete(self, position_id, revision, account_ids=()):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('UPDATE walt_positions SET deleted=1,revision=revision+1,updated_at=? '
                                'WHERE id=? AND revision=? AND deleted=0', (now().isoformat(), position_id, revision))
            if result.rowcount != 1:
                raise Conflict('Позиция изменена или удалена. Обнови список.')
            self._capture(db, 'position_removed', account_ids)

    def set_fx(self, rate, as_of, source, revision, account_ids=()):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = db.execute('UPDATE asset_fx SET usd_rub=?,as_of=?,source=?,revision=revision+1 '
                                'WHERE id=1 AND revision=?', (format(Decimal(str(rate)), 'f'), str(as_of), source, revision))
            if result.rowcount != 1:
                raise Conflict('Курс уже изменён. Обнови страницу.')
            self._capture(db, 'fx_updated', account_ids)
            return dict(db.execute('SELECT * FROM asset_fx WHERE id=1').fetchone())

    def summary(self, account_ids=()):
        with self.connect() as db, localcontext() as ctx:
            ctx.prec = 80
            return self._summary(db, account_ids)

    def _summary(self, db, account_ids):
        positions = [dict(r) for r in db.execute('SELECT * FROM walt_positions WHERE deleted=0 ORDER BY kind,symbol')]
        fx = dict(db.execute('SELECT * FROM asset_fx WHERE id=1').fetchone())
        rate = Decimal(fx['usd_rub']) if fx['usd_rub'] else None
        classes = {}
        walt_known = Decimal(0)
        missing = []
        for p in positions:
            value = Decimal(p['quantity']) * Decimal(p['unit_price_usd']) if p['unit_price_usd'] is not None else None
            p['value_usd'] = amount(value)
            p['price_source'] = 'manual_walt'
            if value is None:
                missing.append(p['symbol'])
            else:
                walt_known += value
                key = p['kind']
                classes[key] = classes.get(key, Decimal(0)) + value
        broker = []
        # Latest snapshot for EACH account: do not drop accounts with an older snapshot.
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='portfolio_snapshots'").fetchone()
        if exists:
            args = tuple(account_ids)
            where = 'WHERE s.account_id IN (' + ','.join('?' for _ in args) + ')' if args else ''
            broker = [dict(r) for r in db.execute('''SELECT s.* FROM portfolio_snapshots s JOIN
                (SELECT account_id,MAX(day) day FROM portfolio_snapshots GROUP BY account_id) latest
                ON s.account_id=latest.account_id AND s.day=latest.day ''' + where + ' ORDER BY s.account_id', args)]
        missing_accounts = sorted(set(account_ids) - {r['account_id'] for r in broker})
        broker_known = sum((Decimal(r['total_rub']) for r in broker), Decimal(0))
        walt_total = walt_known if positions and not missing else None
        broker_total = broker_known if broker and not missing_accounts else None
        combined = broker_total + walt_total * rate if broker_total is not None and walt_total is not None and rate else None
        allocation = []
        broker_classes = [('bonds_rub', 'Облигации · Т-Инвестиции'), ('shares_rub', 'Акции · Т-Инвестиции'),
                          ('etf_rub', 'Фонды · Т-Инвестиции'), ('cash_rub', 'Деньги · Т-Инвестиции')]
        component_total = Decimal(0)
        for key, label in broker_classes:
            value = sum((Decimal(r[key]) for r in broker), Decimal(0))
            component_total += value
            if value:
                allocation.append({'label': label, 'source': 'tinvest', 'value_rub': amount(value)})
        if broker_known != component_total:
            allocation.append({'label': 'Прочее / разница · Т-Инвестиции', 'source': 'tinvest',
                               'value_rub': amount(broker_known - component_total)})
        for key, value in classes.items():
            allocation.append({'label': KINDS[key] + ' · Walt', 'source': 'walt',
                               'value_usd': amount(value), 'value_rub': amount(value * rate) if rate else None})
        warnings = []
        if not positions:
            warnings.append('Позиции Walt ещё не внесены; отсутствие данных не означает нулевой баланс.')
        if missing:
            warnings.append('Нет цены: ' + ', '.join(missing) + '. Эти позиции не включены в известную сумму.')
        if not broker:
            warnings.append('Нет сохранённого снимка Т-Инвестиций.')
        if missing_accounts:
            warnings.append('Нет снимков части настроенных счетов Т-Инвестиций.')
        if not rate:
            warnings.append('Укажи курс USD/RUB для общей оценки.')
        stamps = sorted({p['valued_on'] for p in positions} | {r['day'] for r in broker} | ({fx['as_of']} if fx['as_of'] else set()))
        if len(stamps) > 1:
            warnings.append('Источники оценены на разные даты. Общая сумма объединяет последние доступные оценки.')
        today = now().date().isoformat()
        if stamps and stamps[0] < today:
            warnings.append('Есть оценки за прошлые даты. Проверь актуальность цен, количества и курса.')
        return {'positions': positions, 'fx': fx, 'kinds': KINDS, 'warnings': warnings,
                'broker': {'accounts': broker, 'total_rub': amount(broker_total), 'known_total_rub': amount(broker_known),
                           'missing_accounts': missing_accounts},
                'walt': {'has_data': bool(positions), 'total_usd': amount(walt_total), 'known_total_usd': amount(walt_known),
                         'missing_prices': missing},
                'combined': {'complete': combined is not None, 'total_rub': amount(combined),
                             'total_usd': amount(combined / rate) if combined is not None else None},
                'allocation': allocation, 'return_pct': None}

    def _capture(self, db, event, account_ids):
        with localcontext() as ctx:
            ctx.prec = 80
            payload = self._summary(db, account_ids)
        db.execute('INSERT INTO asset_valuation_history(captured_at,event,payload) VALUES(?,?,?)',
                   (now().isoformat(), event, json.dumps(payload, ensure_ascii=False)))

    def capture(self, account_ids=()):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._capture(db, 'manual_snapshot', account_ids)

    def history(self):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM asset_valuation_history ORDER BY id DESC LIMIT 30').fetchall()
            return [{'id': r['id'], 'captured_at': r['captured_at'], 'event': r['event'],
                     **{k: json.loads(r['payload'])[k] for k in ('walt', 'combined')}} for r in rows]
