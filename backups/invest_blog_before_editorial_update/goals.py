"""SQLite-backed financial goals. Values are observed, never forecast as returns."""
from __future__ import annotations

import calendar
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .bond_analytics import _coverage
from .posts import COUPON, DIVIDEND

GOAL_TYPES = {
    'portfolio_value': ('Стоимость портфеля', 'RUB'),
    'monthly_income': ('Средний месячный купонно-дивидендный доход', 'RUB'),
    'monthly_coupons': ('Средний месячный купонный доход', 'RUB'),
    'bond_share': ('Доля облигаций', '%'),
}


def _validate(goal: dict) -> dict:
    if not isinstance(goal, dict):
        raise ValueError('Тело запроса должно быть объектом')
    name = goal.get('name', '')
    kind = goal.get('kind')
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80):
        raise ValueError('Название должно содержать 1–80 символов')
    if kind not in GOAL_TYPES:
        raise ValueError('Неизвестный тип финансовой цели')
    try:
        amount = Decimal(str(goal['target']))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError('Укажи числовое целевое значение') from exc
    if not amount.is_finite() or amount <= 0 or amount > Decimal('1000000000000000'):
        raise ValueError('Цель должна быть положительной конечной величиной')
    if kind == 'bond_share' and amount > 100:
        raise ValueError('Доля облигаций должна быть не выше 100%')
    due = goal.get('due_date') or None
    if due is not None:
        if not isinstance(due, str):
            raise ValueError('Дата должна быть строкой YYYY-MM-DD')
        try:
            due = date.fromisoformat(due).isoformat()
        except ValueError as exc:
            raise ValueError('Дата должна иметь формат YYYY-MM-DD') from exc
    note = goal.get('note') or ''
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError('Примечание — не более 500 символов')
    return {'name': name.strip(), 'kind': kind, 'target': str(amount),
            'due_date': due, 'note': note.strip()}


class GoalStore:
    def __init__(self, portfolio_store):
        self.portfolio = portfolio_store
        with portfolio_store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS financial_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
                due_date TEXT, note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )''')

    def list(self):
        with self.portfolio.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM financial_goals ORDER BY id')]

    def upsert(self, payload: dict, goal_id: int | None = None):
        clean = _validate(payload)
        now = datetime.now(timezone.utc).isoformat()
        with self.portfolio.connect() as db:
            if goal_id is None:
                cursor = db.execute('''INSERT INTO financial_goals
                    (name,kind,target,due_date,note,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)''', (*clean.values(), now, now))
                goal_id = cursor.lastrowid
            else:
                result = db.execute('''UPDATE financial_goals SET name=?,kind=?,target=?,due_date=?,note=?,updated_at=? WHERE id=?''',
                                     (*clean.values(), now, goal_id))
                if result.rowcount == 0:
                    raise KeyError(goal_id)
            row = db.execute('SELECT * FROM financial_goals WHERE id=?', (goal_id,)).fetchone()
        return dict(row)

    def delete(self, goal_id: int):
        with self.portfolio.connect() as db:
            result = db.execute('DELETE FROM financial_goals WHERE id=?', (goal_id,))
        if result.rowcount == 0:
            raise KeyError(goal_id)


def _previous_months(day: date, count: int = 12):
    y, m = day.year, day.month
    for _ in range(count):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        start = date(y, m, 1)
        finish = date(y, m, calendar.monthrange(y, m)[1])
        yield start, finish


def current_measures(store) -> dict:
    day = store.latest_day()
    if day is None:
        return {'as_of': None, 'portfolio_value': None, 'bond_share': None,
                'monthly_income': None, 'monthly_coupons': None, 'verified_months': 0}
    with store.connect() as db:
        agg = db.execute('''SELECT SUM(CAST(total_rub AS REAL)) AS total,
            SUM(CAST(bonds_rub AS REAL)) AS bonds FROM portfolio_snapshots WHERE day=?''', (day,)).fetchone()
        current_accounts = {r['account_id'] for r in db.execute('SELECT account_id FROM portfolio_snapshots WHERE day=?',(day,))}
        all_accounts = {r['account_id'] for r in db.execute('SELECT account_id FROM portfolio_snapshots')}
        ranges = {}
        for row in db.execute('SELECT account_id,first_day,last_day FROM operation_import_ranges'):
            ranges.setdefault(row['account_id'], []).append(
                (date.fromisoformat(row['first_day']), date.fromisoformat(row['last_day'])))
        full_snapshots = bool(current_accounts and current_accounts == all_accounts)
        month_ranges = list(_previous_months(date.fromisoformat(day)))
        valid = [(start,finish) for start,finish in month_ranges if current_accounts and
                 all(_coverage(ranges.get(acc, []), start, finish) for acc in current_accounts)]
        coupon_sum = Decimal('0'); dividend_sum = Decimal('0')
        for first,last in valid:
            for row in db.execute('''SELECT account_id,kind,currency,amount FROM broker_operations
                WHERE substr(executed_at,1,10) BETWEEN ? AND ?''', (first.isoformat(), last.isoformat())):
                if row['account_id'] not in current_accounts or row['currency'] != 'RUB':
                    continue
                if row['kind'] in COUPON:
                    coupon_sum += Decimal(row['amount'])
                elif row['kind'] in DIVIDEND:
                    dividend_sum += Decimal(row['amount'])
    total = Decimal(str(agg['total'])) if agg['total'] is not None else None
    bonds = Decimal(str(agg['bonds'])) if agg['bonds'] is not None else None
    n = len(valid)
    # 1-2 complete months do not establish a useful average. No zero-filling missing months.
    monthly_coupon = round(float(coupon_sum / n), 2) if n >= 3 and full_snapshots else None
    monthly_income = round(float((coupon_sum + dividend_sum) / n), 2) if n >= 3 and full_snapshots else None
    return {'as_of': day, 'portfolio_value': round(float(total), 2) if total is not None and full_snapshots else None,
            'bond_share': round(float(bonds/total*100), 2) if total and total > 0 and bonds is not None and full_snapshots else None,
            'monthly_income': monthly_income, 'monthly_coupons': monthly_coupon,
            'verified_months': n, 'monthly_basis': 'Фактические купоны и дивиденды, без вычета налогов и комиссий; только полные загруженные месяцы; минимум 3 месяца.'}


def evaluate_goals(goal_store: GoalStore):
    measures = current_measures(goal_store.portfolio)
    result = []
    for row in goal_store.list():
        target = Decimal(row['target'])
        cur = measures.get(row['kind'])
        value = Decimal(str(cur)) if cur is not None else None
        progress = round(float(value / target * 100), 1) if value is not None else None
        complete = bool(value is not None and value >= target)
        past_due = bool(row['due_date'] and row['due_date'] < date.today().isoformat())
        result.append({**row, 'type_label': GOAL_TYPES[row['kind']][0],
                       'unit': GOAL_TYPES[row['kind']][1], 'current': cur,
                       'progress_pct': progress, 'complete': complete,
                       'status': 'unknown' if value is None else 'complete' if complete else 'overdue' if past_due else 'in_progress'})
    return {'as_of': measures['as_of'], 'measures': measures, 'goals': result,
            'disclaimer': 'Текущее значение и прогресс — не прогноз достижения к дате. Доход — среднее по доступным полным месяцам, не гарантированная будущая выплата.'}
