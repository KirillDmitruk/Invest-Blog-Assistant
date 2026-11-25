"""Account-scoped, explainable bond review. No orders, invented ratings or FX rates."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from .model import money
from .issuer_reference import issuer_key
from .portfolio_store import PortfolioStore

API_SOURCE = 'https://developer.tbank.ru/invest/services/instruments/methods'
LABELS = {'low': 'Ниже', 'moderate': 'Умеренный', 'high': 'Повышенный', 'unknown': 'Нет оценки'}
ORDER = {'low': 0, 'moderate': 1, 'high': 2, 'unknown': 3}
DEFAULT_POLICY = {'issuer_limit_pct': 15, 'high_risk_limit_pct': 20, 'rating_max_days': 365}


def number(raw):
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def day_of(raw):
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def percent(value, total):
    return round(float(value / total * 100), 2) if total > 0 else None


def safe_url(raw):
    if not isinstance(raw, str) or len(raw) > 1000:
        return None
    try:
        url = urlsplit(raw)
        return raw if url.scheme == 'https' and url.hostname and not url.username else None
    except ValueError:
        return None


def rating_bucket(level):
    """Only explicitly Russian national scales; international AAA is not interchangeable."""
    text = str(level or '').strip().upper().replace(' ', '')
    national = text.startswith('RU') or text.endswith('(RU)') or text.endswith('.RU')
    if not national:
        return None
    text = re.sub(r'^RU|\(RU\)$|\.RU$', '', text)
    if not re.fullmatch(r'(AAA|AA|A|BBB|BB|B|CCC|CC|C|SD|RD|D)[+-]?', text):
        return None
    if text.startswith('AAA') or text.startswith('AA'):
        return 'low'
    if text.startswith('BBB') or re.fullmatch('A[+-]?', text):
        return 'moderate'
    return 'high'


def ratings_for(meta, evidence, today, max_days):
    entries = []
    for raw in meta.get('ratings', []) if isinstance(meta.get('ratings'), list) else []:
        if not isinstance(raw, dict):
            continue
        entries.append({'level': raw.get('ratingLevel'), 'agency': raw.get('agencyName'),
                        'date': raw.get('ratingDate'), 'forecast': raw.get('forecast'),
                        'watch': raw.get('isUnderWatch') is True, 'source': API_SOURCE, 'via': 'T-Invest'})
    if evidence.get('rating'):
        entries.append({'level': evidence['rating'], 'agency': evidence.get('rating_agency'),
                        'date': evidence.get('rating_date'), 'source': evidence.get('source_url'),
                        'forecast': None, 'watch': False, 'via': 'Проверено вручную'})
    result = []
    for item in entries:
        stamp = day_of(item.get('date'))
        age = (today - stamp).days if stamp else None
        bucket = rating_bucket(item['level'])
        valid = bool(item['agency'] and item['source'] and bucket and age is not None and 0 <= age <= max_days)
        result.append(dict(item, date=stamp.isoformat() if stamp else None,
                           usable=valid, risk=bucket if valid else None))
    # Keep only the newest observation of each agency; a historical lower rating
    # from the same agency must not override its newer rating. Conflicting agencies: worst.
    latest = {}
    for item in result:
        key = str(item['agency']).strip().casefold()
        if key not in latest or (item['date'] or '') > (latest[key]['date'] or ''):
            latest[key] = item
    usable = [r for r in latest.values() if r['usable']]
    bucket = max((r['risk'] for r in usable), key=lambda x: ORDER[x], default=None)
    return result, bucket


class ReviewStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS iis_review_runs (
                    id INTEGER PRIMARY KEY, account_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    report_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_iis_reviews ON iis_review_runs(account_id,id);
                CREATE TABLE IF NOT EXISTS iis_review_evidence (
                    instrument_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS iis_review_policy (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS iis_issuer_reference (
                    isin TEXT PRIMARY KEY, payload_json TEXT NOT NULL, fetched_on TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS iis_review_job (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), token TEXT,
                    started_at TEXT, expires_at TEXT, last_error TEXT);
                INSERT OR IGNORE INTO iis_review_job(singleton) VALUES(1);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def policy(self):
        with self.connect() as db:
            row = db.execute('SELECT payload_json FROM iis_review_policy WHERE singleton=1').fetchone()
        return dict(DEFAULT_POLICY, **(json.loads(row[0]) if row else {}))

    def save_policy(self, payload):
        if not isinstance(payload, dict) or set(payload) != set(DEFAULT_POLICY):
            raise ValueError('Нужны три лимита политики')
        bounds = {'issuer_limit_pct': (1, 100), 'high_risk_limit_pct': (0, 100), 'rating_max_days': (1, 730)}
        for key, (lo, hi) in bounds.items():
            val = payload[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not lo <= val <= hi:
                raise ValueError('Некорректный лимит: ' + key)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO iis_review_policy VALUES(1,?)', (json.dumps(payload),))
        return payload

    def evidence(self):
        with self.connect() as db:
            return {r['instrument_id']: json.loads(r['payload_json']) for r in db.execute('SELECT * FROM iis_review_evidence')}

    def issuer_data(self, today):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM iis_issuer_reference').fetchall()
        return {r['isin']: json.loads(r['payload_json']) for r in rows
                if 0 <= (today - date.fromisoformat(r['fetched_on'])).days <= 30}

    def save_issuer(self, isin, payload, today):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO iis_issuer_reference VALUES(?,?,?)',
                       (isin, json.dumps(payload, ensure_ascii=False), today.isoformat()))

    def save_evidence(self, uid, payload):
        fields = {'issuer_id', 'issuer_name', 'sector', 'rating', 'rating_agency', 'rating_date', 'source_url', 'note'}
        if not isinstance(uid, str) or not 1 <= len(uid) <= 100 or not isinstance(payload, dict) or set(payload) - fields:
            raise ValueError('Некорректная карточка')
        result = {}
        for key in fields:
            value = payload.get(key, '')
            if not isinstance(value, str) or len(value) > (1000 if key in ('note', 'source_url') else 200):
                raise ValueError('Некорректное поле: ' + key)
            result[key] = value.strip()
        if any(result[k] for k in ('issuer_id', 'issuer_name', 'sector', 'rating')) and not safe_url(result['source_url']):
            raise ValueError('Для фактов нужна HTTPS-ссылка на источник')
        if bool(result['issuer_id']) != bool(result['issuer_name']):
            raise ValueError('Для эмитента нужны и идентификатор (например ИНН), и название')
        if result['rating']:
            stamp = day_of(result['rating_date'])
            if not stamp or stamp > date.today() or not result['rating_agency'] or not rating_bucket(result['rating']):
                raise ValueError('Нужны национальный рейтинг (например ruAA или AA(RU)), агентство и дата не в будущем')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO iis_review_evidence VALUES(?,?,?)',
                       (uid, json.dumps(result, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        return result

    def latest(self, account_id):
        with self.connect() as db:
            row = db.execute('SELECT id,created_at,report_json FROM iis_review_runs WHERE account_id=? ORDER BY id DESC LIMIT 1', (account_id,)).fetchone()
        return dict(json.loads(row['report_json']), run_id=row['id'], created_at=row['created_at']) if row else None

    def history(self, account_id):
        with self.connect() as db:
            rows = db.execute('SELECT id,created_at,report_json FROM iis_review_runs WHERE account_id=? ORDER BY id DESC LIMIT 20', (account_id,)).fetchall()
        return [{'id': r['id'], 'created_at': r['created_at'], 'summary': json.loads(r['report_json'])['summary']} for r in rows]

    def append(self, account_id, report, now):
        with self.connect() as db:
            cur = db.execute('INSERT INTO iis_review_runs(account_id,created_at,report_json) VALUES(?,?,?)',
                             (account_id, now.isoformat(), json.dumps(report, ensure_ascii=False, allow_nan=False)))
        return cur.lastrowid

    def job(self):
        with self.connect() as db:
            row = dict(db.execute('SELECT * FROM iis_review_job WHERE singleton=1').fetchone())
        expiry = datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None
        return {'running': bool(row['token'] and expiry and expiry > datetime.now(timezone.utc)),
                'started_at': row['started_at'], 'last_error': row['last_error']}

    def claim(self, now):
        token = uuid.uuid4().hex
        with self.connect() as db:
            cur = db.execute('''UPDATE iis_review_job SET token=?,started_at=?,expires_at=?,last_error=NULL
                WHERE singleton=1 AND (token IS NULL OR expires_at<=?)''',
                (token, now.isoformat(), (now + timedelta(hours=2)).isoformat(), now.isoformat()))
        return token if cur.rowcount else None

    def release(self, token, error=None):
        with self.connect() as db:
            db.execute('UPDATE iis_review_job SET token=NULL,expires_at=NULL,last_error=COALESCE(?,last_error) WHERE singleton=1 AND token=?', (error, token))


def analyze_iis(store, account_id, *, today=None, policy=None, evidence=None, placements=None, previous=None,
                placement_sources=None, refresh_warnings=None, issuer_data=None):
    today = today or date.today()
    policy = policy or DEFAULT_POLICY
    evidence = evidence or {}
    issuer_data = issuer_data or {}
    with store.connect() as db:
        snap = db.execute('SELECT * FROM portfolio_snapshots WHERE account_id=? ORDER BY day DESC LIMIT 1', (account_id,)).fetchone()
        if not snap:
            return {'has_data': False, 'account_id': account_id, 'message': 'Для выбранного счёта ещё нет снимка. Запусти анализ с обновлением API.'}
        positions = [dict(p) for p in db.execute('SELECT * FROM position_snapshots WHERE day=? AND account_id=?', (snap['day'], account_id))]
        metas = {}
        for r in db.execute('SELECT * FROM bond_metadata'):
            try:
                raw = json.loads(r['raw_json'])
                if isinstance(raw, dict):
                    metas[r['instrument_id']] = (raw, r['fetched_on'])
            except (ValueError, TypeError):
                pass
    stale = not 0 <= (today - date.fromisoformat(snap['day'])).days <= 3
    warnings = ['Доли эмитентов и риска считаются по текущему RUB-номиналу, а не по рыночной стоимости. Валютные выпуски не пересчитываются в рубли.']
    if stale:
        warnings.append('Снимок старше 3 дней или датирован будущим. Выводы требуют обновления.')
    rows = []
    for pos in positions:
        if pos['kind'].lower() not in ('bond', 'instrument_type_bond'):
            continue
        qty = number(pos['quantity'])
        if qty is None or qty <= 0:
            continue
        uid = pos['instrument_id']
        meta, fetched = metas.get(uid, ({}, None))
        extra = evidence.get(uid, evidence.get(meta.get('isin'), {}))
        ratings, bucket = ratings_for(meta, extra, today, policy['rating_max_days'])
        broker = {1: 'low', 2: 'moderate', 3: 'high', 'RISK_LEVEL_LOW': 'low', 'RISK_LEVEL_MODERATE': 'moderate', 'RISK_LEVEL_HIGH': 'high'}.get(meta.get('riskLevel'))
        source = 'Национальный рейтинг' if bucket else 'Уровень брокера' if broker else 'Нет данных'
        risk = bucket or broker or 'unknown'
        reasons, gaps = [], []
        nominal, cur = money(meta['nominal']) if isinstance(meta.get('nominal'), dict) else (None, None)
        principal = qty * nominal if nominal is not None and nominal > 0 and cur == 'RUB' else None
        reference = issuer_data.get(meta.get('isin'), {})
        issuer = issuer_key(extra.get('issuer_id') or reference.get('issuer_id') or meta.get('issuerUid') or meta.get('issuer_uid')) or None
        issuer_name = extra.get('issuer_name') or reference.get('issuer_name') or meta.get('issuerName') or issuer
        maturity = day_of(pos['maturity_date'] or meta.get('maturityDate'))
        age = (today - day_of(fetched)).days if day_of(fetched) else None
        meta_stale = age is None or not 0 <= age <= 7
        if meta_stale:
            gaps.append('Карточка отсутствует или старше 7 дней')
        if not bucket:
            gaps.append('Нет актуального распознанного национального рейтинга')
        if not issuer:
            gaps.append('Не подтверждён эмитент; название выпуска не используется для группировки')
        if principal is None:
            gaps.append('Нет текущего положительного RUB-номинала')
        if not maturity and meta.get('perpetualFlag') is not True:
            gaps.append('Нет даты погашения')
        if not all(isinstance(meta.get(k), bool) for k in ('subordinatedFlag', 'perpetualFlag')):
            gaps.append('Не подтверждены признаки субординации / бессрочности')
        for field, label in [('subordinatedFlag', 'Субординированный долг'), ('perpetualFlag', 'Бессрочная бумага')]:
            if meta.get(field) is True:
                reasons.append(label)
                risk = 'high'
        if broker == 'high':
            reasons.append('Высокий риск по классификации брокера')
            risk = 'high'
        if meta.get('liquidityFlag') is False:
            reasons.append('Брокер не подтверждает достаточную ликвидность')
        if any(r['usable'] and (r.get('watch') or 'негатив' in str(r.get('forecast', '')).lower()) for r in ratings):
            reasons.append('Негативный прогноз или наблюдение агентства')
        if maturity and maturity <= today:
            reasons.append('Дата погашения наступила; проверить расчёты и остаток')
        call = day_of(meta.get('callDate'))
        if call and today <= call <= today + timedelta(days=90):
            reasons.append('Оферта в ближайшие 90 дней: проверить тип и сроки подачи')
        price = number(pos['price'])
        nkd = number(pos['nkd'])
        # GetPortfolio.currentPrice is MoneyValue per instrument, unlike candle points.
        market = qty * (price + nkd) if pos['currency'] == 'RUB' and price is not None and nkd is not None and price >= 0 else None
        rows.append({'id': uid, 'name': pos['name'], 'ticker': pos['ticker'], 'isin': meta.get('isin'),
                     'quantity': float(qty), 'principal_rub': float(principal) if principal is not None else None,
                     'market_value_rub': float(market) if market is not None else None,
                     'issuer_id': issuer, 'issuer_name': issuer_name, 'sector': extra.get('sector') or meta.get('sector') or 'Не указан',
                     'issuer_source': extra.get('source_url') or reference.get('source_url') or (API_SOURCE if issuer else None),
                     'risk': risk, 'risk_label': LABELS[risk], 'risk_source': source, 'ratings': ratings,
                     'maturity': maturity.isoformat() if maturity else None, 'offer_date': call.isoformat() if call else None,
                     'floating': meta.get('floatingCouponFlag'), 'amortizing': meta.get('amortizationFlag'),
                     'subordinated': meta.get('subordinatedFlag'), 'perpetual': meta.get('perpetualFlag'),
                     'for_iis': meta.get('forIisFlag'), 'metadata_updated': fetched,
                     'reasons': reasons, 'gaps': gaps, 'evidence': extra,
                     'metadata_stale': meta_stale, 'has_rating': bucket is not None})
    known = sum((number(r['principal_rub']) or Decimal(0) for r in rows), Decimal(0))
    complete = bool(rows) and all(r['principal_rub'] is not None for r in rows)
    issuer_complete = complete and all(r['issuer_id'] for r in rows)
    groups = defaultdict(lambda: {'principal': Decimal(0), 'issues': [], 'name': ''})
    risk_sums = defaultdict(lambda: Decimal(0))
    maturity_sums = defaultdict(lambda: Decimal(0))
    sector_sums = defaultdict(lambda: Decimal(0))
    for row in rows:
        value = number(row['principal_rub']) or Decimal(0)
        risk_sums[row['risk']] += value
        maturity_sums[(row['maturity'] or 'Не указано')[:4] if row['maturity'] else 'Не указано'] += value
        sector_sums[row['sector']] += value
        row['share_known_principal_pct'] = percent(value, known) if row['principal_rub'] is not None else None
        if row['issuer_id']:
            group = groups[row['issuer_id']]
            group['principal'] += value
            group['issues'].append(row['id'])
            group['name'] = row['issuer_name']
    high_pct = percent(risk_sums['high'], known)
    for row in rows:
        group = groups.get(row['issuer_id'])
        row['issuer_share_pct'] = percent(group['principal'], known) if group else None
        if group and row['issuer_share_pct'] is not None and row['issuer_share_pct'] > policy['issuer_limit_pct']:
            row['reasons'].append(f"Доля эмитента {row['issuer_share_pct']}% выше лимита {policy['issuer_limit_pct']}%")
        if row['risk'] == 'high' and high_pct is not None and high_pct > policy['high_risk_limit_pct']:
            row['reasons'].append('Превышен лимит повышенного риска в облигационной части')
        if row['risk'] == 'high' or row['reasons']:
            row['action'] = 'review'
            row['action_label'] = 'Пересмотреть / возможное сокращение'
        elif stale or row['gaps'] or row['risk'] == 'unknown':
            row['action'] = 'verify'
            row['action_label'] = 'Сначала проверить данные'
        else:
            row['action'] = 'hold'
            row['action_label'] = 'Наблюдать; явных нарушений не найдено'
    if not complete:
        warnings.append('Не все выпуски оценены по RUB-номиналу. Проценты относятся только к известной части; докупки не предлагаются.')
    if not issuer_complete:
        warnings.append('Группировка эмитентов неполная. Разные выпуски без подтверждённого идентификатора не считаются диверсификацией.')
    rows.sort(key=lambda r: ({'review': 0, 'verify': 1, 'hold': 2}[r['action']], -(r['principal_rub'] or 0)))
    candidates = []
    seen = set()
    sources = {s['source']: s for s in placement_sources or []}
    for item in placements or []:
        key = item.get('isin') or item.get('uid') or str(item['id'])
        if key in seen:
            continue
        seen.add(key)
        extra = evidence.get(item.get('uid'), evidence.get(item.get('isin'), {}))
        meta = {'ratings': item.get('ratings') or [], 'riskLevel': item.get('risk_level')}
        if item.get('credit_rating') and safe_url(item.get('rating_source_url')):
            meta['ratings'] = []
            extra = dict({'rating': item.get('credit_rating'), 'rating_agency': item.get('rating_agency'),
                          'rating_date': item.get('rating_date'), 'source_url': item.get('rating_source_url')}, **extra)
        rating_list, bucket = ratings_for(meta, extra, today, policy['rating_max_days'])
        source_status = sources.get(item.get('source'))
        source_fresh = placement_sources is None or bool(source_status and source_status['ok'] and day_of(source_status['checked_at'])
                                                        and 0 <= (today - day_of(source_status['checked_at'])).days <= 3)
        reference = issuer_data.get(item.get('isin'), {})
        issuer = issuer_key(extra.get('issuer_id') or reference.get('issuer_id') or item.get('issuer_uid')) or None
        current = percent(groups[issuer]['principal'], known) if issuer in groups else 0 if issuer and issuer_complete else None
        eligible = (item.get('for_iis') is True and issuer_complete and issuer and bucket == 'low'
                    and current is not None and current < policy['issuer_limit_pct'] and not stale
                    and item.get('subordinated') is False and item.get('perpetual') is False
                    and item.get('risk_level') not in ('RISK_LEVEL_HIGH', 3) and source_fresh and not refresh_warnings
                    and not any(r['metadata_stale'] for r in rows)
                    and not any(r['usable'] and (r.get('watch') or 'негатив' in str(r.get('forecast', '')).lower()) for r in rating_list))
        reasons = []
        if item.get('for_iis') is not True:
            reasons.append('Доступность для ИИС не подтверждена')
        if not issuer:
            reasons.append('Не подтверждён эмитент')
        if not bucket:
            reasons.append('Нет актуального национального рейтинга')
        if current is not None and current >= policy['issuer_limit_pct']:
            reasons.append('Эмитент уже достиг лимита: новый выпуск не уменьшает концентрацию')
        if not issuer_complete or stale:
            reasons.append('Нужно обновить или дополнить данные портфеля')
        if not source_fresh:
            reasons.append('Источник размещения не обновлён или последняя проверка завершилась ошибкой')
        if refresh_warnings:
            reasons.append('Синхронизация прошла с предупреждениями: сначала проверить их')
        if item.get('risk_level') in ('RISK_LEVEL_HIGH', 3):
            reasons.append('Высокий риск по классификации брокера')
        if bucket in ('moderate', 'high'):
            reasons.append('Риск выше выбранного фильтра кандидатов (AA-/ruAA- и выше)')
        if item.get('subordinated') is not False or item.get('perpetual') is not False:
            reasons.append('Сложная структура долга или её признаки не подтверждены')
        if any(r['metadata_stale'] for r in rows):
            reasons.append('Нужны свежие карточки текущих облигаций')
        candidates.append({'id': item['id'], 'key': item.get('uid') or item.get('isin') or key,
                           'name': item['name'], 'isin': item.get('isin'), 'date': item.get('placement_date'),
                           'risk': bucket or 'unknown', 'ratings': rating_list, 'issuer_share_pct': current,
                           'status': 'research' if eligible else 'verify',
                           'status_label': 'Кандидат для изучения условий' if eligible else 'Проверить / пропустить до уточнения',
                           'reasons': reasons or ['Перед покупкой сравнить доходность к оферте/погашению, спред, комиссии, объём торгов и финансовую отчётность.'],
                           'source_url': safe_url(item.get('source_url')), 'evidence': extra})
    old = {r['id']: r for r in (previous or {}).get('bonds', [])}
    changes = []
    for row in rows:
        past = old.pop(row['id'], None)
        if not past and previous:
            changes.append({'name': row['name'], 'detail': 'Новая позиция в портфеле'})
        elif past:
            parts = []
            if past['risk'] != row['risk']:
                parts.append(f"Категория: {LABELS[past['risk']]} → {row['risk_label']}")
            if past.get('quantity') != row['quantity']:
                parts.append(f"Количество: {past.get('quantity')} → {row['quantity']}")
            if past.get('ratings') != row['ratings']:
                parts.append('Изменились рейтинги или их актуальность')
            if past.get('action') != row['action']:
                parts.append('Изменился приоритет проверки')
            if parts:
                changes.append({'name': row['name'], 'detail': '; '.join(parts)})
    changes.extend({'name': r['name'], 'detail': 'Позиции больше нет в снимке'} for r in old.values())
    distribution = [{'risk': key, 'label': LABELS[key], 'count': sum(r['risk'] == key for r in rows),
                     'principal_rub': float(risk_sums[key]), 'pct': percent(risk_sums[key], known)} for key in LABELS]
    concentration_actions = [{'issuer': val['name'], 'excess_principal_rub': round(float(val['principal'] - known * Decimal(str(policy['issuer_limit_pct'])) / 100), 2),
                              'detail': 'Не наращивать эмитента; изучить сокращение или разбавление будущими пополнениями. Сумма — превышение по номиналу, не цена продажи.'}
                             for val in groups.values() if known > 0 and val['principal'] / known * 100 > policy['issuer_limit_pct']]
    return {'has_data': True, 'account_id': account_id, 'as_of': snap['day'], 'captured_at': snap['captured_at'],
            'stale': stale, 'policy': policy, 'bonds': rows, 'risk_distribution': distribution,
            'issuers': [{'id': key, 'name': val['name'], 'count': len(val['issues']), 'principal_rub': float(val['principal']),
                         'pct': percent(val['principal'], known)} for key, val in sorted(groups.items(), key=lambda p: -p[1]['principal'])],
            'maturities': [{'year': k, 'principal_rub': float(v)} for k, v in sorted(maturity_sums.items())],
            'sectors': [{'name': k, 'principal_rub': float(v)} for k, v in sorted(sector_sums.items())],
            'other_assets': [{'name': p['name'], 'kind': p['kind'], 'quantity': p['quantity']} for p in positions if p['kind'].lower() not in ('bond', 'instrument_type_bond')],
            'summary': {'bond_count': len(rows), 'known_principal_rub': float(known), 'total_rub': float(number(snap['total_rub']) or 0),
                        'review_count': sum(r['action'] == 'review' for r in rows), 'unknown_count': sum(r['risk'] == 'unknown' for r in rows),
                        'issuer_coverage_pct': percent(sum((g['principal'] for g in groups.values()), Decimal(0)), known),
                        'principal_complete': complete, 'issuer_complete': issuer_complete},
            'candidates': candidates, 'changes': changes, 'warnings': warnings, 'concentration_actions': concentration_actions,
            'methodology': ['AA− и выше: ниже кредитный риск; A / BBB: умеренный; BB и ниже: повышенный. Это настраиваемая аналитическая группировка национальных рейтингов, не вероятность дефолта.',
                            'Если рейтинга нет, отображается отдельный уровень брокера; отсутствие рейтинга не означает низкий риск.',
                            'Субординация, бессрочность, концентрация и негативный прогноз повышают приоритет проверки.',
                            'Пересмотр не означает немедленную продажу: сначала проверить отчётность, ликвидность, цену выхода и комиссии. Продажа внутри счёта и вывод средств с ИИС — разные действия.',
                            'Доходность к погашению, дюрация и фундаментальная платёжеспособность не рассчитываются без необходимых данных. Купонная ставка не подменяет доходность.']}


def run_review(settings, *, account_id=None, force=True, api=None, scan=True, now=None, enrich_issuers=True, enrich_portfolio=False):
    from .placements import PlacementMonitor, PlacementStore
    from .sync import PortfolioSync
    from .tinvest import TInvest
    now = now or datetime.now(timezone.utc)
    account_id = account_id or getattr(settings, 'iis_review_account_id', '') or (settings.account_ids[0] if len(settings.account_ids) == 1 else None)
    if not account_id or account_id not in settings.account_ids:
        raise ValueError('Выбери один счёт из TINVEST_ACCOUNT_IDS; для автоанализа нескольких счетов задай IIS_REVIEW_ACCOUNT_ID')
    settings.validate_api()
    reviews = ReviewStore(settings.db_path)
    previous = reviews.latest(account_id)
    interval = getattr(settings, 'iis_review_interval_days', 3)
    if not force and previous and now < datetime.fromisoformat(previous['created_at']) + timedelta(days=interval):
        return {'skipped': True, 'run_id': previous['run_id']}
    token = reviews.claim(now)
    if not token:
        return {'busy': True}
    try:
        # Recheck after the cross-process lease, so two scheduled workers cannot
        # both decide that the same review is due.
        previous = reviews.latest(account_id)
        if not force and previous and now < datetime.fromisoformat(previous['created_at']) + timedelta(days=interval):
            return {'skipped': True, 'run_id': previous['run_id']}
        api = api or TInvest(settings.invest_token, settings.timezone)
        today = now.astimezone(settings.timezone).date()
        account = next((a for a in api.accounts() if a.get('id') == account_id), None)
        if account is None:
            raise ValueError('Настроенный счёт не найден среди открытых счетов API')
        portfolio = PortfolioStore(settings.db_path)
        synced = PortfolioSync(api, (account_id,), portfolio).run(today, days=14, force_metadata=True)
        if enrich_portfolio:
            from .iis_portfolio import collect_profiles
            synced['warnings'].extend(collect_profiles(api, portfolio, account_id, today))
        placements = PlacementStore(settings.db_path)
        if scan:
            PlacementMonitor(placements, api, feed_url=settings.placements_feed_url,
                             finam_enabled=settings.placements_finam_enabled).scan(today)
        future = placements.upcoming(today)
        issuer_warnings = []
        if enrich_issuers:
            from .issuer_reference import IssuerReference
            reference = IssuerReference()
            known_issuers = reviews.issuer_data(today)
            with portfolio.connect() as db:
                raw = db.execute('''SELECT m.raw_json FROM bond_metadata m JOIN position_snapshots p
                    ON p.instrument_id=m.instrument_id WHERE p.account_id=? AND p.day=?''', (account_id, today.isoformat())).fetchall()
            isins = set()
            for row in raw:
                try:
                    value = json.loads(row['raw_json'])
                    if isinstance(value, dict):
                        isins.add(value.get('isin'))
                except (ValueError, TypeError):
                    pass
            isins.update(p.get('isin') for p in future)
            if enrich_portfolio:
                from .iis_portfolio import profiles
                isins.update(p['data'].get('isin') for p in profiles(portfolio).values())
            failures = 0
            for isin in sorted(s for s in isins if isinstance(s, str) and s)[:200]:
                if isin in known_issuers:
                    continue
                try:
                    value = reference.lookup(isin)
                    if value:
                        reviews.save_issuer(isin, value, today)
                except Exception:
                    failures += 1
                    if failures >= 3:
                        issuer_warnings.append('Справочник эмитентов MOEX недоступен; используются только сохранённые подтверждённые сведения.')
                        break
        report = analyze_iis(portfolio, account_id, today=today, policy=reviews.policy(), evidence=reviews.evidence(),
                             placements=future, previous=previous, issuer_data=reviews.issuer_data(today),
                             placement_sources=placements.status(), refresh_warnings=synced['warnings'])
        report['warnings'].extend(issuer_warnings)
        report['account'] = {'name': account.get('name'), 'type': account.get('type'),
                             'is_iis': account.get('type') in ('ACCOUNT_TYPE_TINKOFF_IIS', 2)}
        report['sync_warnings'] = synced['warnings']
        report['placement_sources'] = placements.status()
        report['run_id'] = reviews.append(account_id, report, now)
        report['created_at'] = now.isoformat()
        return report
    except Exception as exc:
        # Do not persist arbitrary network exception strings, which may contain secrets.
        reviews.release(token, 'Обновление не завершено: ' + type(exc).__name__ + '. Проверь настройки и журнал приложения.')
        raise
    finally:
        reviews.release(token)
