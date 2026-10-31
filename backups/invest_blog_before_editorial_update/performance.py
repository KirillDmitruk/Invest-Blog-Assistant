"""Conservative portfolio return analysis.

Never treats the reconstructed historical market-value curve as observed broker
valuation. The chained Modified Dietz approximation is NOT exact TWR where an
external cash flow occurred without a valuation at that exact instant.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import NamedTuple

from .bond_analytics import _coverage
from .portfolio_store import PortfolioStore

INPUTS = frozenset({'OPERATION_TYPE_INPUT', 'OPERATION_TYPE_INPUT_ACQUIRING',
                    'OPERATION_TYPE_INPUT_SWIFT', 'OPERATION_TYPE_INP_MULTI'})
OUTPUTS = frozenset({'OPERATION_TYPE_OUTPUT', 'OPERATION_TYPE_OUTPUT_SWIFT',
                     'OPERATION_TYPE_OUT_MULTI', 'OPERATION_TYPE_OUTPUT_ACQUIRING'})
NON_CASH_TRANSFERS = frozenset({'OPERATION_TYPE_INPUT_SECURITIES', 'OPERATION_TYPE_OUTPUT_SECURITIES'})


class Flow(NamedTuple):
    day: date
    value: Decimal  # + incoming external contribution; - external withdrawal
    kind: str
    when: datetime


@dataclass
class History:
    accounts: set[str]
    valuations: dict[date, Decimal]
    flows: list[Flow]
    ranges: dict[str, list[tuple[date, date]]]
    obstacles: list[tuple[date, str]]
    incomplete_days: set[date]
    capture_times: dict[date, tuple[datetime, datetime] | None]


def _decimal(raw) -> Decimal:
    value = Decimal(str(raw))
    if not value.is_finite():
        raise ValueError('Неконечная денежная сумма')
    return value


def load_history(store: PortfolioStore) -> History:
    with store.connect() as db:
        # Requiring the union of all accounts prevents optimistic aggregation.
        accounts = {r[0] for r in db.execute('SELECT DISTINCT account_id FROM portfolio_snapshots')}
        valuations_by_day: dict[date, dict[str, Decimal]] = defaultdict(dict)
        timestamps_by_day: dict[date, dict[str, datetime | None]] = defaultdict(dict)
        for r in db.execute('SELECT day,account_id,total_rub,captured_at FROM portfolio_snapshots ORDER BY day'):
            day = date.fromisoformat(r['day'])
            valuations_by_day[day][r['account_id']] = _decimal(r['total_rub'])
            try:
                when = datetime.fromisoformat(r['captured_at'])
                # Old date-only records/naive values don't prove the intraday cutoff.
                if when.tzinfo is None or when.utcoffset() is None:
                    when = None
            except (ValueError,TypeError):
                when = None
            timestamps_by_day[day][r['account_id']] = when
        valuations = {day: sum(values.values(), Decimal(0)) for day, values in valuations_by_day.items()
                      if accounts and set(values) == accounts}
        missing = {d for d, vals in valuations_by_day.items() if set(vals) != accounts}
        capture_times = {}
        for d in valuations:
            times = list(timestamps_by_day[d].values())
            capture_times[d] = (min(times), max(times)) if times and all(t is not None for t in times) else None
        ranges: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for r in db.execute('SELECT account_id,first_day,last_day FROM operation_import_ranges'):
            ranges[r['account_id']].append((date.fromisoformat(r['first_day']), date.fromisoformat(r['last_day'])))
        flows = []
        obstacles = []
        for r in db.execute('SELECT executed_at,kind,amount,currency FROM broker_operations ORDER BY executed_at'):
            typ = r['kind'] or ''
            if typ not in INPUTS | OUTPUTS | NON_CASH_TRANSFERS and not (
                typ.startswith('OPERATION_TYPE_INPUT_') or typ.startswith('OPERATION_TYPE_OUTPUT_')):
                continue
            day = date.fromisoformat(r['executed_at'][:10])
            try:
                when = datetime.fromisoformat(r['executed_at'])
                if when.tzinfo is None or when.utcoffset() is None:
                    raise ValueError('Timezone missing')
            except (TypeError,ValueError):
                obstacles.append((day,'Операция без времени и часового пояса'))
                continue
            if typ in NON_CASH_TRANSFERS:
                obstacles.append((day, 'Перевод ценных бумаг без подтверждённой стоимости'))
                continue
            if typ not in INPUTS | OUTPUTS:
                obstacles.append((day, f'Неизвестный внешний денежный поток: {typ}'))
                continue
            if str(r['currency']).upper() not in ('RUB', 'RUR'):
                obstacles.append((day, 'Внешний поток не в рублях: нужен подтверждённый курс'))
                continue
            try:
                magnitude = abs(_decimal(r['amount']))
            except (InvalidOperation, ValueError, TypeError):
                obstacles.append((day, 'Некорректная сумма внешнего денежного потока'))
                continue
            if magnitude == 0:
                obstacles.append((day, 'Внешний денежный поток с нулевой суммой'))
                continue
            flows.append(Flow(day, magnitude if typ in INPUTS else -magnitude, typ, when))
    return History(accounts, valuations, flows, ranges, obstacles, missing, capture_times)


def _xirr(flows: list[tuple[date, Decimal]]) -> float | None:
    """Return unique IRR root; multiple/no roots become unavailable, never 0%."""
    merged: dict[date, float] = defaultdict(float)
    for day, amount in flows:
        merged[day] += float(amount)
    points = [(d, amt) for d, amt in sorted(merged.items()) if abs(amt) > 1e-9]
    if len(points) < 2 or not (any(v < 0 for _, v in points) and any(v > 0 for _, v in points)):
        return None
    start = points[0][0]
    def npv(rate: float) -> float:
        return sum(amt / ((1 + rate) ** ((d-start).days / 365.25)) for d, amt in points)
    # Broad deterministic scan: negative returns to high positive IRRs; detect ambiguous roots.
    rates = [-0.999999] + [-1.0 + 10 ** (a / 10) for a in range(-59, 41)]
    rates = sorted(set(x for x in rates if -1 < x <= 1000))
    roots = []
    previous_rate = rates[0]
    try:
        previous_value = npv(previous_rate)
    except (OverflowError, ZeroDivisionError):
        return None
    for rate in rates[1:]:
        try:
            current = npv(rate)
        except (OverflowError, ZeroDivisionError):
            return None
        if not isfinite(current) or not isfinite(previous_value):
            previous_rate, previous_value = rate, current
            continue
        if previous_value == 0:
            roots.append(previous_rate)
        if previous_value * current < 0:
            lo, hi, lo_value = previous_rate, rate, previous_value
            for _ in range(110):
                mid = (lo + hi) / 2
                mv = npv(mid)
                if not isfinite(mv):
                    return None
                if mv * lo_value > 0:
                    lo, lo_value = mid, mv
                else:
                    hi = mid
            roots.append((lo + hi) / 2)
        previous_rate, previous_value = rate, current
    if previous_value == 0:
        roots.append(previous_rate)
    uniq = []
    for r in roots:
        if not any(abs(r-v) < 1e-7 for v in uniq):
            uniq.append(r)
    return uniq[0] if len(uniq) == 1 and isfinite(uniq[0]) else None


def evaluate(history: History, start: date, end: date, *, detailed=False) -> dict:
    result = {'status': 'unavailable', 'from': start.isoformat(), 'to': end.isoformat(),
              'twr_pct': None, 'xirr_pct': None, 'profit_rub': None,
              'net_contributions_rub': None, 'start_value_rub': None, 'end_value_rub': None,
              'method': 'chained_modified_dietz', 'exact_twr': False, 'reason': '', 'warnings': []}
    if end <= start:
        result['reason'] = 'Нужны минимум два разных дня оценки'
        return result
    if start not in history.valuations or end not in history.valuations:
        result['reason'] = 'Для начала и конца периода нужны реальные снимки всех счетов'
        return result
    if any(start < d <= end for d in history.incomplete_days):
        result['reason'] = 'В периоде обнаружены снимки не всех счетов'
        return result
    if not history.accounts or any(not _coverage(history.ranges.get(a, []), start, end)
                                   for a in history.accounts):
        result['reason'] = 'История операций не покрывает весь период по всем счетам'
        return result
    obstacles = sorted({label for day,label in history.obstacles if start <= day <= end})
    if obstacles:
        result['reason'] = '; '.join(obstacles[:3])
        return result
    valuations = [(d, v) for d, v in sorted(history.valuations.items()) if start <= d <= end]
    if len(valuations) < 2 or any(v <= 0 for _, v in valuations):
        result['reason'] = 'Для расчёта нужны положительные значения стоимости портфеля'
        return result
    # Imported operations can be newer than a broker snapshot from the same
    # day. Respect the actual capture timestamps (the sync anchors them at the
    # START of the portfolio API request), otherwise returns are distorted.
    boundaries = [d for d, _ in valuations]
    if any(history.capture_times.get(d) is None for d in boundaries):
        if any(start <= f.day <= end for f in history.flows):
            result['reason'] = 'Нет точного времени снимков для сопоставления пополнений и выводов'
            return result
        # No external cash flows: valuation-only return remains well-defined.
        cutoff = {d:(datetime.combine(d,time.max,tzinfo=timezone.utc),)*2 for d in boundaries}
    else:
        cutoff = {d:history.capture_times[d] for d in boundaries}
    for f in history.flows:
        if not (start <= f.day <= end):
            continue
        for d in boundaries:
            lo, hi = cutoff[d]
            if lo < f.when <= hi:
                result['reason'] = 'Внешняя операция произошла между снимками разных счетов; нужен согласованный снимок'
                return result
    first_time = cutoff[start][0]
    last_time = cutoff[end][0]
    flows = [f for f in history.flows if first_time < f.when <= last_time]
    total_contributions = sum((f.value for f in flows), Decimal(0))
    start_value, end_value = history.valuations[start], history.valuations[end]
    profit = end_value - start_value - total_contributions
    result.update({'start_value_rub': round(float(start_value),2), 'end_value_rub': round(float(end_value),2),
                   'net_contributions_rub': round(float(total_contributions),2), 'profit_rub': round(float(profit),2),
                   'external_flows': len(flows), 'snapshots': len(valuations)})
    chain = Decimal(1)
    has_weighted_flow = False
    for (d0, v0), (d1, v1) in zip(valuations, valuations[1:]):
        t0, t1 = cutoff[d0][0], cutoff[d1][0]
        step_flows = [f for f in flows if t0 < f.when <= t1]
        net = sum((f.value for f in step_flows), Decimal(0))
        span = Decimal(str((t1-t0).total_seconds()))
        if span <= 0:
            result['reason'] = 'Некорректный порядок времени снимков'
            return result
        weighted = sum((f.value * Decimal(str((t1-f.when).total_seconds())) / span
                        for f in step_flows), Decimal(0))
        denominator = v0 + weighted
        if denominator <= 0:
            result['reason'] = 'Неположительная база расчёта Modified Dietz'
            return result
        multiplier = Decimal(1) + (v1 - v0 - net) / denominator
        if multiplier <= 0:
            result['reason'] = 'Возврат не может быть рассчитан из данного потока и стоимости'
            return result
        chain *= multiplier
        if step_flows:
            has_weighted_flow = True
    result['twr_pct'] = round(float((chain - 1) * 100), 4)
    result['exact_twr'] = not has_weighted_flow
    if has_weighted_flow:
        result['warnings'].append('TWR — приближение: сцепленный Modified Dietz, нет оценок непосредственно в моменты пополнений и выводов.')
    irr_flows = [(start,-start_value)] + [(f.day,-f.value) for f in flows] + [(end,end_value)]
    irr = _xirr(irr_flows)
    if irr is not None:
        result['xirr_pct'] = round(irr*100,4)
    else:
        result['warnings'].append('XIRR не рассчитан: денежные потоки не имеют единственного обнаруженного корня.')
    if (end-start).days < 30:
        result['warnings'].append('Годовая XIRR за период короче 30 дней может быть нестабильной.')
    result['status'] = 'ok'
    return result


def _months_before(day: date, months: int) -> date:
    year = day.year
    month = day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(day.day, calendar.monthrange(year,month)[1]))


def summarize(store: PortfolioStore, start: date | None = None, end: date | None = None) -> dict:
    history = load_history(store)
    all_days = sorted(history.valuations)
    if not all_days:
        return {'status':'unavailable', 'reason':'Пока нет сохранённых полных снимков портфеля',
                'period':None,'comparisons':[],'history':[],'cashflows':[], 'notes':[]}
    if end is None:
        end = all_days[-1]
    if start is None:
        start = all_days[0]
    current = evaluate(history,start,end)
    comparisons = []
    for months,label in ((1,'1 месяц'),(3,'3 месяца'),(6,'6 месяцев'),(12,'12 месяцев')):
        target = _months_before(end,months)
        candidates = [d for d in all_days if target <= d <= target+timedelta(days=7) and d < end]
        actual_start = min(candidates) if candidates else target
        entry = evaluate(history,actual_start,end)
        entry['label'] = label
        entry['target_start'] = target.isoformat()
        comparisons.append(entry)
    return {'status':current['status'], 'reason':current.get('reason',''), 'period':current,
            'comparisons':comparisons,
            'history':[{'date':d.isoformat(),'value_rub':round(float(v),2), 'source':'broker_snapshot'}
                       for d,v in sorted(history.valuations.items())],
            'cashflows':[{'date':f.day.isoformat(),'amount_rub':round(float(f.value),2),'type':f.kind}
                         for f in history.flows if (history.capture_times.get(start) and
                         history.capture_times.get(end) and
                         history.capture_times[start][0] < f.when <= history.capture_times[end][0])],
            'notes':['Доходность использует только сохранённые полные снимки брокера, НЕ восстановленную историю цен.',
                     'Для точной TWR нужны оценки портфеля непосредственно перед каждым внешним денежным потоком.',
                     'XIRR годовая, зависит от дат и размеров пополнений/выводов; купоны и дивиденды внутри портфеля — не внешние потоки.',
                     'Сумма результата до налогов/комиссий не корректируется отдельно: используется брокерская оценка портфеля.',
                     'Периоды с неполными данными обозначены как недоступные.']}
