"""v1.2: evidence-based fixed-income analytics from saved positions & coupon schedules.

All projections use *today's holdings*, not a historical holding reconstruction.
Unknown coupon amounts stay unknown; currencies are never silently converted.
"""
from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .model import quotation, money
from .posts import COUPON, DIVIDEND, REPAYMENT, DEPOSIT


def add_months(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = m + 1
    return date(y, month, min(day.day, calendar.monthrange(y, month)[1]))


def month_floor(day: date) -> date:
    return day.replace(day=1)


def _scalar(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _coverage(intervals: list[tuple[date, date]], start: date, end: date) -> bool:
    cursor = start
    for first, last in sorted(intervals):
        if last < cursor:
            continue
        if first > cursor:
            return False
        cursor = max(cursor, last + timedelta(days=1))
        if cursor > end:
            return True
    return cursor > end


def monthly_cashflows(store, as_of: date) -> dict:
    """Show 12 calendar months ending this month; partial/unknown periods are explicit."""
    first = add_months(month_floor(as_of), -11)
    with store.connect() as db:
        accounts = [r[0] for r in db.execute('SELECT DISTINCT account_id FROM portfolio_snapshots WHERE day=?', (as_of.isoformat(),))]
        placeholders = ','.join('?' for _ in accounts)
        rows = (db.execute(f'''SELECT substr(executed_at,1,7) month,kind,currency,amount,instrument_id
            FROM broker_operations WHERE substr(executed_at,1,10)>=? AND substr(executed_at,1,10)<=?
            AND account_id IN ({placeholders})''',
            (first.isoformat(), as_of.isoformat(), *accounts)).fetchall() if accounts else [])
        names = {r['instrument_id']:r['name'] for r in db.execute(
            'SELECT instrument_id,name FROM instrument_details')}
        names.update({r['instrument_id']:r['name'] for r in db.execute(
            'SELECT instrument_id,name FROM position_snapshots WHERE day=?',(as_of.isoformat(),))})
        coverage = defaultdict(list)
        for r in db.execute('SELECT account_id,first_day,last_day FROM operation_import_ranges'):
            coverage[r['account_id']].append((date.fromisoformat(r['first_day']), date.fromisoformat(r['last_day'])))
    kinds = {'coupons':COUPON, 'dividends':DIVIDEND, 'repayments':REPAYMENT, 'deposits':DEPOSIT}
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))
    coupon_payers = defaultdict(Decimal)
    for op in rows:
        if op['kind'] in COUPON and op['instrument_id']:
            coupon_payers[(op['instrument_id'], op['currency'])] += Decimal(op['amount'])
        for category, operation_types in kinds.items():
            if op['kind'] in operation_types:
                grouped[op['month']][category][op['currency']] += Decimal(op['amount'])
                break
    months = []
    for offset in range(12):
        begin = add_months(first, offset)
        finish = min(add_months(begin, 1) - timedelta(days=1), as_of)
        month = begin.strftime('%Y-%m')
        status = ('no_coverage' if not accounts or not all(_coverage(coverage.get(a, []), begin, finish) for a in accounts)
                  else 'partial' if finish < add_months(begin, 1) - timedelta(days=1) else 'complete')
        values = {category:{currency:_scalar(amount) for currency,amount in sorted(grouped[month][category].items())}
                  for category in kinds}
        months.append({'month':month,'status':status,**values})
    payers = sorted(coupon_payers.items(),key=lambda x: abs(x[1]), reverse=True)
    return {'months':months,
            'top_coupon_sources':[{'id':uid,'name':names.get(uid,uid),'currency':currency,
                                    'amount':_scalar(amount)} for (uid,currency),amount in payers[:15]],
            'coverage_note': 'Значение 0 подтверждено только для полностью загруженных периодов. Текущий месяц неполный.'}


def get_bond_analytics(store, today: date | None = None) -> dict:
    """Read-only analysis; does not query broker or mutate state."""
    snapshot = store.latest_day()
    if not snapshot:
        return {'has_data':False,'message':'Сначала запусти sync --days 365 для загрузки портфеля и купонного календаря.'}
    as_of = date.fromisoformat(snapshot)
    # The calendar is anchored to the latest snapshot, rather than to wall-clock time.
    # If caller supplies today (e.g. tests), it can narrow the visible future horizon.
    start = max(as_of, today) if today else as_of
    end = add_months(month_floor(start), 12)
    with store.connect() as db:
        positions = db.execute('''SELECT instrument_id,account_id,name,ticker,kind,quantity,price,currency,nkd,maturity_date
               FROM position_snapshots WHERE day=? AND lower(kind) IN ('bond','instrument_type_bond')''', (snapshot,)).fetchall()
        raw_meta = {r['instrument_id']:r for r in db.execute('SELECT * FROM bond_metadata')}
        raw_refresh = {r['instrument_id']:r for r in db.execute('SELECT * FROM bond_calendar_refresh')}
        event_rows = db.execute('''SELECT instrument_id,coupon_date,coupon_number,payment,currency,coupon_type
                  FROM bond_coupon_events WHERE coupon_date>=? AND coupon_date<?
                  ORDER BY coupon_date,instrument_id,coupon_number''', (start.isoformat(),end.isoformat())).fetchall()
    aggregated = {}
    for p in positions:
        uid=p['instrument_id']; qty=Decimal(p['quantity'])
        entry=aggregated.setdefault(uid,{'id':uid,'name':p['name'],'ticker':p['ticker'],'quantity':Decimal(0),
            'kind':p['kind'],'maturity':p['maturity_date'], 'currency':p['currency'],
            'price_value':Decimal(0),'priced':True,'nkd_value':Decimal(0), 'nkd_known':True})
        entry['quantity']+=qty
        if p['price'] is None or p['currency'] != entry['currency']:
            entry['priced']=False
        else:
            entry['price_value']+=qty*Decimal(p['price'])
        if p['nkd'] is None:
            entry['nkd_known']=False
        else:
            entry['nkd_value']+=qty*Decimal(p['nkd'])
    calendar = defaultdict(list)
    for ev in event_rows:
        if ev['instrument_id'] in aggregated:
            calendar[ev['instrument_id']].append(ev)
    monthly = {add_months(month_floor(start),i).strftime('%Y-%m'):
        {'month':add_months(month_floor(start),i).strftime('%Y-%m'),
         'known':defaultdict(Decimal),'events':0,'unknown_events':0} for i in range(12)}
    events=[]; bonds=[]; missing=[]; stale=[]; unknown_count=0
    for uid, row in sorted(aggregated.items(),key=lambda item:item[1]['name']):
        meta_row = raw_meta.get(uid)
        meta = json.loads(meta_row['raw_json']) if meta_row else {}
        refresh = raw_refresh.get(uid)
        if not refresh:
            missing.append(row['name'])
        elif refresh['fetched_on'] < start.isoformat():
            stale.append(row['name'])
        nominal,nominal_cur=money(meta.get('nominal')) if meta.get('nominal') else (None,None)
        flags = (('Флоатер' if meta.get('floatingCouponFlag') else
                  'Фиксированный / другой') if meta_row else 'Нет данных')
        # Stale calendars are shown as unavailable, never aggregated as valid forecasts.
        all_events=[] if not refresh or refresh['fetched_on'] < start.isoformat() else calendar[uid]
        total_coupon_known=defaultdict(Decimal)
        next_date=None; next_sum=None; next_currency=None
        for e in all_events:
            pay=Decimal(e['payment']) if e['payment'] is not None else None
            # No published positive coupon amount: do not present a 0-income forecast.
            amount = pay*row['quantity'] if pay is not None and pay>0 and e['currency'] else None
            if amount is None: unknown_count+=1
            if amount is not None:
                total_coupon_known[e['currency']]+=amount
            d=e['coupon_date']; m=d[:7]
            if m not in monthly:continue
            monthly[m]['events']+=1
            if amount is None:
                monthly[m]['unknown_events']+=1
            else:
                monthly[m]['known'][e['currency']]+=amount
            events.append({'date':d,'id':uid,'name':row['name'],'ticker':row['ticker'],
                'coupon_number':e['coupon_number'], 'quantity':_scalar(row['quantity']),
                'per_bond':_scalar(pay),'amount':_scalar(amount), 'currency':e['currency'],
                'coupon_type':e['coupon_type'],'estimated':True})
            if next_date is None or d<next_date:
                next_date=d;next_sum=_scalar(amount);next_currency=e['currency']
        bonds.append({'id':uid,'name':row['name'],'ticker':row['ticker'],'quantity':_scalar(row['quantity']),
            'market_value_native':_scalar(row['price_value']) if row['priced'] else None,
            'currency':row['currency'], 'nkd_native':_scalar(row['nkd_value']) if row['nkd_known'] else None,
            'isin':meta.get('isin') or None, 'nominal':_scalar(nominal), 'nominal_currency':nominal_cur,
            'coupon_frequency':meta.get('couponQuantityPerYear') or None, 'coupon_structure':flags,
            'floating':bool(meta.get('floatingCouponFlag')) if meta_row else None,
            'amortizing':bool(meta.get('amortizationFlag')) if meta_row else None,
            'perpetual':bool(meta.get('perpetualFlag')) if meta_row else None,
            'maturity_date':row['maturity'] or (meta.get('maturityDate') or '')[:10] or None,
            'next_coupon_date':next_date,'next_coupon_amount':next_sum,'next_coupon_currency':next_currency,
            'known_coupon_12m':{c:_scalar(v) for c,v in sorted(total_coupon_known.items())},
            'calendar_updated':refresh['fetched_on'] if refresh else None,
            'metadata_updated':meta_row['fetched_on'] if meta_row else None})
    events.sort(key=lambda item:(item['date'],item['name'],item['coupon_number']))
    total=defaultdict(Decimal)
    for r in monthly.values():
        for c,v in r['known'].items():total[c]+=v
    result_months=[]
    for r in monthly.values():
        result_months.append({'month':r['month'],'known':{c:_scalar(v) for c,v in sorted(r['known'].items())},
                              'events':r['events'],'unknown_events':r['unknown_events']})
    return {'has_data':True,'as_of':snapshot,'snapshot_stale':start>as_of,'calendar_from':start.isoformat(),
        'calendar_to_exclusive':end.isoformat(), 'bonds':bonds,'bond_count':len(bonds),
        'calendar':{'months':result_months,'events':events,'known_total':{c:_scalar(v) for c,v in sorted(total.items())},
            'missing_bonds':missing,'stale_bonds':stale,'unknown_amount_events':unknown_count,
            'covered_bonds':len(aggregated)-len(missing)-len(stale),
            'disclaimer':'Оценка по текущему количеству бумаг и опубликованным купонам. Не гарантирует выплату; по флоатерам будущая сумма может измениться. Налоги не вычтены.'},
        'actual_income':monthly_cashflows(store,as_of)}
