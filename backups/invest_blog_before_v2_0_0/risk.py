"""v1.3: conservative, explainable checks for a long-term portfolio.

No broker requests, no trading actions, no invented issuer/rating classifications.
The score only covers supported checks, never denotes safety or expected returns.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .model import money

ZERO = Decimal('0')
HUNDRED = Decimal('100')


def _d(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pct(a: Decimal, b: Decimal) -> float | None:
    return round(float(a / b * HUNDRED), 2) if b > 0 else None


def _finding(key: str, severity: str, title: str, detail: str, suggestion: str) -> dict:
    return dict(key=key, severity=severity, title=title, detail=detail, suggestion=suggestion)


def analyze_portfolio(store, *, bond_target: float = .60, max_position: float = .15,
                      today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    if not 0 <= bond_target <= 1 or not 0 < max_position <= 1:
        raise ValueError('Неверные лимиты долей портфеля')
    day = store.latest_day()
    if not day:
        return {'has_data': False, 'message': 'Нет снимков. Выполни uv run python -m invest_blog.cli sync --days 365.'}
    with store.connect() as db:
        agg = db.execute('''SELECT COUNT(DISTINCT account_id) accounts,
            SUM(CAST(total_rub AS REAL)) total, SUM(CAST(bonds_rub AS REAL)) bonds,
            SUM(CAST(shares_rub AS REAL)) shares, SUM(CAST(etf_rub AS REAL)) etf,
            SUM(CAST(cash_rub AS REAL)) cash FROM portfolio_snapshots WHERE day=?''', (day,)).fetchone()
        latest_by_account = db.execute('''SELECT account_id, MAX(day) day FROM portfolio_snapshots GROUP BY account_id''').fetchall()
        position_rows = db.execute('''SELECT instrument_id,name,ticker,kind,quantity,price,currency,maturity_date
                FROM position_snapshots WHERE day=?''', (day,)).fetchall()
        meta = {}
        for row in db.execute('SELECT instrument_id,raw_json FROM bond_metadata'):
            try:
                val = json.loads(row['raw_json'])
                if isinstance(val, dict):
                    meta[row['instrument_id']] = val
            except (TypeError, json.JSONDecodeError):
                continue

    total = _d(agg['total']) or ZERO
    bonds_total = _d(agg['bonds']) or ZERO
    bond_pct = _pct(bonds_total, total)
    findings: list[dict] = []
    checks: list[dict] = []
    issues: list[str] = []
    snapshot_age = (today - date.fromisoformat(day)).days
    fresh = 0 <= snapshot_age <= 3
    if not fresh:
        issues.append(f'Снимку портфеля {snapshot_age} дн. (возможно, часовой пояс или обновление устарело).')
    if not agg['accounts'] or len(latest_by_account) != agg['accounts'] or any(r['day'] != day for r in latest_by_account):
        issues.append('Не все счета синхронизированы на одну дату: объединённые показатели могут быть неполными.')
    full_accounts = not any('Не все счета' in s for s in issues)

    def check(key, title, weight, fraction, explanation, confidence='confirmed'):
        if fraction is None:
            checks.append(dict(key=key, title=title, weight=weight, status='unknown', score=None,
                               reason=explanation, confidence='insufficient'))
            return
        value = max(ZERO, min(Decimal('1'), fraction))
        status = 'ok' if value >= Decimal('.999') else 'attention' if value > Decimal('.5') else 'high'
        checks.append(dict(key=key, title=title, weight=weight, status=status,
                           score=round(float(value * weight), 1), reason=explanation, confidence=confidence))

    if total <= 0 or not full_accounts:
        check('allocation', 'Доля облигаций', 25, None, 'Нет полного снимка стоимости по всем счетам.')
    else:
        distance = abs(Decimal(str(bond_target * 100)) - Decimal(str(bond_pct)))
        # Zero penalty within 5 percentage points; full penalty at 35 points.
        fraction = Decimal('1') - max(ZERO, distance - Decimal('5')) / Decimal('30')
        check('allocation', 'Соответствие целевой доле облигаций', 25, fraction,
              f'Фактически {bond_pct:.1f}% при ориентире {bond_target*100:.0f}%.')
        if distance > 5:
            findings.append(_finding('allocation', 'attention', 'Структура отличается от ориентира',
                 f'Доля облигаций {bond_pct:.1f}% при выбранной цели {bond_target*100:.0f}%.',
                 'Проверь цель и распределение будущих пополнений; это не сигнал совершать сделку.'))

    # Principal face value (not market price!) is used for comparisons *inside* bonds.
    # Issuer attribution is only accepted when the API explicitly supplies issuerUid.
    principal_known = ZERO
    principal_unknown_issues = 0
    issuer_values = defaultdict(lambda: ZERO)
    issuer_counts = defaultdict(int)
    maturity_values = defaultdict(lambda: ZERO)
    total_bond_rows = 0
    valid_principal_rows = 0
    missing_issuer_value = ZERO
    missing_maturity_value = ZERO
    for pos in position_rows:
        if str(pos['kind']).lower() not in ('bond', 'instrument_type_bond'):
            continue
        total_bond_rows += 1
        uid = pos['instrument_id']
        info = meta.get(uid, {})
        qty = _d(pos['quantity'])
        face, currency = money(info.get('nominal')) if isinstance(info.get('nominal'), dict) else (None, None)
        if qty is None or qty <= ZERO or face is None or face <= ZERO or str(currency).upper() != 'RUB':
            principal_unknown_issues += 1
            continue
        principal = qty * face
        valid_principal_rows += 1
        principal_known += principal
        issuer = info.get('issuerUid') or info.get('issuer_uid')
        if isinstance(issuer, str) and issuer.strip():
            issuer_values[issuer] += principal
            issuer_counts[issuer] += 1
        else:
            missing_issuer_value += principal
        maturity = (pos['maturity_date'] or info.get('maturityDate') or '')[:10]
        try:
            if maturity and date.fromisoformat(maturity) >= date.fromisoformat(day):
                maturity_values[maturity[:4]] += principal
            else:
                missing_maturity_value += principal
        except ValueError:
            missing_maturity_value += principal

    # A complete assessment requires every currently held bond to have a confirmed
    # RUB nominal, and >=90% of principal to have the applicable source metadata.
    principal_complete = bool(total_bond_rows and valid_principal_rows == total_bond_rows)
    issuer_cover = float((principal_known - missing_issuer_value) / principal_known) if principal_known else 0
    maturity_cover = float((principal_known - missing_maturity_value) / principal_known) if principal_known else 0
    issuer_coverage_ok = principal_complete and issuer_cover >= .90
    maturity_coverage_ok = principal_complete and maturity_cover >= .90
    if total_bond_rows and not principal_complete:
        issues.append(f'У {principal_unknown_issues} выпусков отсутствует подтверждённый текущий номинал RUB: риск по эмитентам/погашениям неполон.')
    if total_bond_rows and not issuer_coverage_ok:
        issues.append('Недостаточно issuerUid или номиналов для надёжной оценки концентрации эмитентов.')
    if total_bond_rows and not maturity_coverage_ok:
        issues.append('Недостаточно дат погашения или номиналов для оценки лестницы погашений.')

    max_issuer = max(issuer_values.values(), default=ZERO)
    max_issuer_share = max_issuer / principal_known if principal_known else ZERO
    if not total_bond_rows:
        check('issuer', 'Концентрация эмитентов', 30, None, 'В портфеле нет облигаций.')
    elif not issuer_coverage_ok or not full_accounts:
        check('issuer', 'Концентрация эмитентов', 30, None, 'Не хватает достоверной группировки выпусков по эмитентам.')
    else:
        limit = Decimal(str(max_position))
        fraction = (Decimal('1') if max_issuer_share <= limit else
                    Decimal('1') - (max_issuer_share-limit)/(Decimal('1')-limit))
        check('issuer', 'Концентрация эмитентов', 30, fraction,
              f'Крупнейший эмитент: {float(max_issuer_share*100):.1f}% известного рублёвого номинала облигаций.',
              confidence='principal_based')
        if max_issuer_share > limit:
            findings.append(_finding('issuer', 'attention', 'Высокая концентрация на эмитенте',
               f'Крупнейший эмитент: {float(max_issuer_share*100):.1f}% рублёвого номинала облигационной части; порог {max_position*100:.0f}%.',
               'Проверь финансовое состояние эмитента, кредитный рейтинг и альтернативы для новых взносов.'))

    maturity_known = sum(maturity_values.values(), ZERO)
    heaviest = max(maturity_values.values(), default=ZERO)
    concentrated_year_share = heaviest / maturity_known if maturity_known else ZERO
    if not maturity_coverage_ok or not full_accounts:
        check('maturity', 'Распределение погашений', 25, None,
              'Даты погашений известны не для всех рублёвых облигаций.')
    else:
        # At or below 40% in one year: no warning. Above 80%: lowest component grade.
        fraction = Decimal('1') - max(ZERO, concentrated_year_share - Decimal('.40')) / Decimal('.40')
        check('maturity', 'Распределение погашений', 25, fraction,
              f'В самом крупном году — {float(concentrated_year_share*100):.1f}% подтверждённого рублёвого номинала.',
              confidence='principal_based')
        if concentrated_year_share > Decimal('.50'):
            year = max(maturity_values, key=maturity_values.get)
            findings.append(_finding('maturity', 'attention', 'Погашения сосредоточены в одном году',
               f'{year}: {float(concentrated_year_share*100):.1f}% известного номинала с датами погашения.',
               'Оцени риск реинвестирования и будущие поступления, не предполагая одинаковых ставок.'))

    if not fresh or not full_accounts:
        check('freshness', 'Актуальность снимка', 20, None, 'Данные устарели или счета не синхронизированы.')
        findings.append(_finding('freshness', 'info', 'Обнови данные портфеля',
            f'Последний сохранённый снимок — {day}.',
            'Запусти uv run python -m invest_blog.cli sync --days 365.'))
    else:
        check('freshness', 'Актуальность данных', 20, Decimal('1'), f'Снимок {day}; возраст {snapshot_age} дн.')

    evaluated = [x for x in checks if x['score'] is not None]
    evaluated_weight = sum(x['weight'] for x in evaluated)
    # Score is a coverage-qualified indicator, not a guarantee of portfolio quality.
    score = (round(sum(x['score'] for x in evaluated) * 100 / evaluated_weight)
             if evaluated_weight >= 65 and fresh and full_accounts else None)
    return {
        'has_data': True, 'as_of': day, 'total_rub': float(total), 'bond_share_pct': bond_pct,
        'health': {'score': score, 'evaluated_weight_pct': evaluated_weight,
                   'label': 'Ограниченная оценка по проверяемым признакам' if score is not None else 'Недостаточно данных для общего балла',
                   'checks': checks},
        'risk_metrics': {'principal_known_rub': round(float(principal_known), 2),
            'principal_coverage_issues': {'complete': principal_complete, 'known': valid_principal_rows, 'total': total_bond_rows},
            'issuer_principal_coverage_pct': round(issuer_cover * 100, 1),
            'maturity_principal_coverage_pct': round(maturity_cover * 100, 1),
            'largest_issuer_pct_of_bond_principal': _pct(max_issuer, principal_known) if issuer_coverage_ok else None,
            'largest_maturity_year_pct': _pct(heaviest, maturity_known) if maturity_coverage_ok else None,
            'maturity_by_year': [{'year': year, 'principal_rub': round(float(value), 2)} for year, value in sorted(maturity_values.items())],
            'issuer_groups': len(issuer_values)},
        'findings': findings, 'data_quality': issues,
        'disclaimer': 'Эвристические проверки риска по доступным данным, не кредитный рейтинг, не инвестиционная рекомендация и не обещание доходности. Неизвестные показатели не считаются безопасными.'
    }
