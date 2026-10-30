"""Upcoming bond placements: broker discovery, optional vetted JSON feed, persistent monitoring.

No order placement, no trading, no automated channel publication.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from html.parser import HTMLParser
import re
import json
from pathlib import Path
import sqlite3
from threading import Lock
from urllib.parse import urlsplit

import requests

from .model import Draft
from .tinvest import TInvest, TInvestError


def _date(value) -> str | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _money(value) -> tuple[float | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    try:
        n = Decimal(str(value.get('units', '0'))) + Decimal(str(value.get('nano', 0))) / Decimal(10**9)
        return float(n), value.get('currency') or None
    except (InvalidOperation, TypeError, ValueError):
        return None, None


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _key(row: dict) -> str:
    return str(row.get('isin') or row.get('uid') or row.get('ticker') or '').strip().upper()


def from_tinvest(raw: dict, today: date) -> dict | None:
    if not isinstance(raw, dict):
        return None
    placement_date = _date(raw.get('placementDate'))
    if not placement_date or placement_date < today.isoformat():
        return None
    identity = _key(raw)
    if not identity:
        return None
    nominal, nominal_ccy = _money(raw.get('nominal'))
    price, price_ccy = _money(raw.get('placementPrice'))
    floating = raw.get('floatingCouponFlag')
    coupon_type = 'Плавающий' if floating is True else 'Не плавающий (по признаку API)' if floating is False else None
    return {
        'source': 'tinvest', 'source_key': identity, 'name': raw.get('name') or identity,
        'isin': raw.get('isin') or None, 'ticker': raw.get('ticker') or None,
        'placement_date': placement_date, 'maturity_date': _date(raw.get('maturityDate')),
        'nominal': nominal, 'nominal_currency': nominal_ccy,
        'placement_price': price, 'price_currency': price_ccy,
        'coupon_frequency': raw.get('couponQuantityPerYear') or None,
        'coupon_type': coupon_type, 'coupon_amount': None, 'coupon_currency': None,
        'offer_date': None,
        'amortization': raw.get('amortizationFlag') if isinstance(raw.get('amortizationFlag'), bool) else None,
        'qualified': raw.get('forQualInvestorFlag') if isinstance(raw.get('forQualInvestorFlag'), bool) else None,
        'source_url': 'https://developer.tbank.ru/invest/api/instruments-service-bonds',
        'uid': raw.get('uid') or raw.get('instrumentUid') or None,
    }


def from_feed(raw: dict, today: date) -> dict | None:
    """Strict documented optional JSON feed, never guess fields from unrelated HTML pages."""
    if not isinstance(raw, dict):
        return None
    placement_date = _date(raw.get('placement_date'))
    if not placement_date or placement_date < today.isoformat():
        return None
    identity = str(raw.get('isin') or raw.get('id') or '').strip().upper()
    name = str(raw.get('name') or '').strip()
    if not identity or not name:
        return None
    url = raw.get('source_url')
    if url and urlsplit(url).scheme != 'https':
        url = None
    return {
        'source': 'feed', 'source_key': identity, 'name': name[:250],
        'isin': raw.get('isin'), 'ticker': raw.get('ticker'),
        'placement_date': placement_date, 'maturity_date': _date(raw.get('maturity_date')),
        'nominal': _number(raw.get('nominal')), 'nominal_currency': raw.get('nominal_currency'),
        'placement_price': _number(raw.get('placement_price')), 'price_currency': raw.get('price_currency'),
        'coupon_frequency': raw.get('coupon_frequency'), 'coupon_type': raw.get('coupon_type'),
        'coupon_amount': _number(raw.get('coupon_amount')), 'coupon_currency': raw.get('coupon_currency'),
        'offer_date': _date(raw.get('offer_date')),
        'amortization': raw.get('amortization') if isinstance(raw.get('amortization'), bool) else None,
        'qualified': raw.get('qualified') if isinstance(raw.get('qualified'), bool) else None,
        'source_url': url, 'uid': None,
        # Credit ratings are not provided by the T-Invest Bonds instrument listing.
        # The optional external feed may supply rating details and a rating-agency source.
        'credit_rating': str(raw.get('credit_rating') or '').strip()[:32] or None,
        'rating_agency': str(raw.get('rating_agency') or '').strip()[:120] or None,
        'rating_date': _date(raw.get('rating_date')),
        'rating_source_url': (str(raw.get('rating_source_url'))[:1000]
                              if isinstance(raw.get('rating_source_url'), str)
                              and urlsplit(raw['rating_source_url']).scheme == 'https'
                              and urlsplit(raw['rating_source_url']).hostname else None),
    }


class _CalendarTable(HTMLParser):
    """Extract only the explicitly labelled upcoming-issues table, not unrelated page text."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_row=False
        self.in_cell=False
        self.cells=[]
        self.cell=''
        self.href=None
        self.cell_href=None
        self.rows=[]
        self.table_id=0
        self.current_table=0

    def handle_starttag(self,tag,attrs):
        if tag=='table':
            self.table_id+=1;self.current_table=self.table_id
        elif tag=='tr':
            self.in_row=True;self.cells=[]
        elif tag in ('td','th') and self.in_row:
            self.in_cell=True;self.cell='';self.cell_href=None
        elif tag=='a' and self.in_cell:
            self.cell_href=self.cell_href or dict(attrs).get('href')

    def handle_data(self,data):
        if self.in_cell:self.cell+=data

    def handle_endtag(self,tag):
        if tag in ('td','th') and self.in_cell:
            self.cells.append((' '.join(self.cell.split()),self.cell_href))
            self.in_cell=False
        elif tag=='tr' and self.in_row:
            if self.cells:self.rows.append((self.current_table,self.cells))
            self.in_row=False


def finam_calendar(html: str, today: date) -> list[dict]:
    """Public calendar fallback. Only name/placement/maturity are trustworthy here."""
    parser=_CalendarTable()
    parser.feed(html)
    started=False
    target_table=None
    result=[]
    for table_id,cells in parser.rows:
        if target_table is not None and table_id != target_table:
            continue
        titles=[c[0].casefold() for c in cells]
        if len(cells)>=4 and 'выпуск' in titles[0] and 'дата' in titles[2]:
            started=True
            target_table=table_id
            continue
        if not started or len(cells)<4:
            continue
        name=cells[0][0]
        # The calendar uses dates dd.mm.yyyy (do not mistake coupon/payment dates for placement).
        day=cells[2][0]
        if not re.fullmatch(r'\d{2}\.\d{2}\.\d{4}',day):
            continue
        try:
            placement=datetime.strptime(day,'%d.%m.%Y').date()
        except ValueError:
            continue
        if placement<today:
            continue
        maturity=None
        try:
            maturity=datetime.strptime(cells[3][0],'%d.%m.%Y').date().isoformat()
        except ValueError:
            pass
        href=cells[0][1] or ''
        url=('https://www.finam.ru'+href if href.startswith('/') and not href.startswith('//')
             else href if href.startswith('https://www.finam.ru/') else 'https://www.finam.ru/calendar/bonds/ibo/')
        # Link is optional; stable normalized name is the identity (a changed date is an update).
        identity=' '.join(name.upper().split())
        if not identity:continue
        result.append({'source':'finam','source_key':identity,'name':name[:250],
                'isin':None,'ticker':None,'placement_date':placement.isoformat(),
                'maturity_date':maturity,'nominal':None,'nominal_currency':None,
                'placement_price':None,'price_currency':None,'coupon_frequency':None,
                'coupon_type':None,'coupon_amount':None,'coupon_currency':None,
                'offer_date':None,'amortization':None,'qualified':None,
                'source_url':url,'uid':None})
    if not started:
        raise ValueError('Финам изменил структуру HTML-календаря: таблица размещений не найдена')
    return result


class PlacementStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS placement_issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL, source_key TEXT NOT NULL,
                    placement_date TEXT NOT NULL, name TEXT NOT NULL,
                    info_json TEXT NOT NULL, hash TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    notified_version INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    UNIQUE(source, source_key)
                );
                CREATE INDEX IF NOT EXISTS idx_placement_date ON placement_issues(placement_date, active);
                CREATE TABLE IF NOT EXISTS placement_checks (
                    source TEXT PRIMARY KEY, checked_at TEXT NOT NULL,
                    ok INTEGER NOT NULL, count INTEGER, error TEXT
                );
                CREATE TABLE IF NOT EXISTS placement_drafts (
                    placement_id INTEGER PRIMARY KEY,
                    draft_id INTEGER NOT NULL
                );
            ''')

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=20000')
        return db

    def save(self, source: str, items: list[dict]) -> dict:
        """Atomic successful scan. Never hide existing results after source failure."""
        now = datetime.now(timezone.utc).isoformat()
        deduped = {str(x['source_key']): x for x in items if x.get('source') == source}
        created = changed = 0
        with self._connect() as db:
            for key, item in deduped.items():
                payload = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                fingerprint = hashlib.sha256(payload.encode()).hexdigest()
                old = db.execute('SELECT id,hash FROM placement_issues WHERE source=? AND source_key=?', (source,key)).fetchone()
                if old is None:
                    db.execute('''INSERT INTO placement_issues(source,source_key,placement_date,name,info_json,hash,first_seen,last_seen)
                                  VALUES(?,?,?,?,?,?,?,?)''', (source,key,item['placement_date'],item['name'],payload,fingerprint,now,now))
                    created += 1
                else:
                    different = old['hash'] != fingerprint
                    db.execute('''UPDATE placement_issues SET placement_date=?,name=?,info_json=?,hash=?,
                                  version=version+?,active=1,last_seen=? WHERE id=?''',
                               (item['placement_date'],item['name'],payload,fingerprint,int(different),now,old['id']))
                    changed += int(different)
            if deduped:
                marks = ','.join('?' for _ in deduped)
                db.execute(f'UPDATE placement_issues SET active=0 WHERE source=? AND source_key NOT IN ({marks})',
                           (source,*deduped))
            else:
                db.execute('UPDATE placement_issues SET active=0 WHERE source=?',(source,))
            db.execute('''INSERT INTO placement_checks(source,checked_at,ok,count,error) VALUES(?,?,1,?,NULL)
                          ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,ok=1,count=excluded.count,error=NULL''',
                       (source,now,len(deduped)))
        return {'source':source,'count':len(deduped),'new':created,'changed':changed}

    def failure(self, source: str, reason: str):
        with self._connect() as db:
            db.execute('''INSERT INTO placement_checks(source,checked_at,ok,count,error) VALUES(?,?,0,NULL,?)
                          ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,ok=0,error=excluded.error''',
                       (source,datetime.now(timezone.utc).isoformat(), reason[:250]))

    def status(self) -> list[dict]:
        with self._connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM placement_checks ORDER BY source')]

    def upcoming(self, today: date | None = None, *, limit: int = 5000) -> list[dict]:
        today = today or date.today()
        with self._connect() as db:
            rows = db.execute('''SELECT id,info_json,version,first_seen,source FROM placement_issues
                      WHERE active=1 AND placement_date>=? ORDER BY placement_date,name LIMIT ?''',
                      (today.isoformat(),limit)).fetchall()
        return [dict(json.loads(r['info_json']), id=r['id'], version=r['version'], first_seen=r['first_seen']) for r in rows]

    def get(self, issue_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute('SELECT * FROM placement_issues WHERE id=? AND active=1 AND placement_date>=?',
                             (issue_id,date.today().isoformat())).fetchone()
        return dict(json.loads(row['info_json']),id=row['id'],version=row['version']) if row else None

    def pending(self, *, limit: int = 5000) -> list[dict]:
        with self._connect() as db:
            rows=db.execute('''SELECT id,info_json,version,notified_version FROM placement_issues
                         WHERE active=1 AND version>notified_version AND placement_date>=?
                         ORDER BY placement_date,id LIMIT ?''',(date.today().isoformat(),limit)).fetchall()
        return [dict(json.loads(r['info_json']), id=r['id'], version=r['version'], is_new=r['notified_version']==0) for r in rows]

    def mark_notified(self, ids_versions: list[tuple[int,int]]):
        with self._connect() as db:
            for issue_id,version in ids_versions:
                db.execute('UPDATE placement_issues SET notified_version=? WHERE id=? AND notified_version<?',
                           (version,issue_id,version))

    def create_draft(self, issue_id: int) -> int:
        """Idempotent selection; atomically link to standard editorial workflow."""
        item = self.get(issue_id)
        if not item:
            raise ValueError('Размещение уже прошло или отсутствует в последней выгрузке')
        body = render_draft(item)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            existing = db.execute('''SELECT d.id,d.state FROM placement_drafts p JOIN drafts d ON p.draft_id=d.id
                                     WHERE p.placement_id=?''',(issue_id,)).fetchone()
            if existing and existing['state'] != 'deleted':
                return existing['id']
            import uuid
            cursor=db.execute('''INSERT INTO drafts(dedup_key,kind,period,body,state,created_at)
                                 VALUES(?,?,?,?,?,?)''',
                              ('placement:'+uuid.uuid4().hex,'placement',item['placement_date'],body,'new',now))
            did=cursor.lastrowid
            db.execute('INSERT INTO placement_drafts(placement_id,draft_id) VALUES(?,?) ON CONFLICT(placement_id) DO UPDATE SET draft_id=excluded.draft_id',
                       (issue_id,did))
        return did


def _formatted(value, currency='') -> str:
    if value is None: return 'уточнить'
    result=f'{value:g}' if isinstance(value,(float,int)) else str(value)
    return (result+' '+(currency or '')).strip()


def render_draft(item: dict) -> str:
    """Short, neutral editorial draft. Never invent credit ratings or issuer facts."""
    def field(key: str, currency_key: str = '') -> str:
        return _formatted(item.get(key), item.get(currency_key) if currency_key else '')

    name = str(item.get('name') or 'Новый выпуск').strip()
    code = item.get('ticker') or item.get('isin') or 'пока не указан'
    coupon_frequency = item.get('coupon_frequency')
    frequency = 'уточнить'
    if isinstance(coupon_frequency, int) and not isinstance(coupon_frequency, bool) and coupon_frequency > 0:
        n = coupon_frequency
        word = ('раз' if n % 10 == 1 and n % 100 != 11 else
                'раза' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'раз')
        frequency = f'{n} {word} в год'
    elif coupon_frequency is not None:
        frequency = f'{_formatted(coupon_frequency)} раз в год'

    # Ratings must come with an agency, rating date and HTTPS source document.
    # Otherwise the author needs to check the agency's official publication.
    rating = ('[проверить рейтинг, агентство и дату]')
    if (item.get('credit_rating') and item.get('rating_agency') and
            _date(item.get('rating_date')) and item.get('rating_source_url') and
            urlsplit(str(item['rating_source_url'])).scheme == 'https' and
            urlsplit(str(item['rating_source_url'])).hostname):
        rating = (f"{item['credit_rating']} — {item['rating_agency']} "
                  f"(на {item['rating_date']})")

    source = {'tinvest': 'T-Invest API', 'finam': 'Финам',
              'feed': 'дополнительный календарь'}.get(item.get('source'), 'календарь')
    lines = [
        f'🛰 На радаре: {name}',
        '', 'Условия нового выпуска',
        f'• Тикер / ISIN: {code}',
        f"• Номинал: {field('nominal', 'nominal_currency')}",
        f"• Цена размещения: {field('placement_price', 'price_currency')}",
        f"• Тип купона: {item.get('coupon_type') or 'уточнить'}",
        f'• Выплаты: {frequency}',
    ]
    if item.get('coupon_amount') is not None:
        lines.append(f"• Купон за выплату: {field('coupon_amount', 'coupon_currency')}")
    lines.extend([
        f'• Кредитный рейтинг: {rating}',
        f"• Размещение: {item.get('placement_date') or 'уточнить'}",
        f"• Погашение: {item.get('maturity_date') or 'уточнить'}",
        f"• Оферта: {item.get('offer_date') or 'уточнить'}",
        '', 'Коротко о компании',
        '[1–2 предложения: чем занимается эмитент и на чём зарабатывает.]',
        '', 'На что обратить внимание',
        '[Одна интересная особенность выпуска и один конкретный риск.]',
        '', 'Мой взгляд',
        '[Короткий личный вывод после проверки условий.]',
        '', f'Источник обнаружения: {source}',
    ])
    url = item.get('source_url')
    if item.get('source') in ('feed', 'finam') and isinstance(url, str) and \
            urlsplit(url).scheme == 'https' and urlsplit(url).hostname:
        lines.append(f'Ссылка: {url}')
    if rating != '[проверить рейтинг, агентство и дату]':
        lines.append(f"Источник рейтинга: {item['rating_source_url']}")
    lines.extend([
        'Условия предварительные — проверьте документы выпуска перед публикацией.',
        'Не индивидуальная инвестиционная рекомендация.',
    ])
    text = '\n'.join(lines)
    if len(text) > 4000:
        raise ValueError('Превышен лимит шаблона')
    return text


class PlacementMonitor:
    _lock = Lock()

    def __init__(self, store: PlacementStore, api: TInvest, *, feed_url: str = '', finam_enabled: bool = True, session=None):
        self.store=store
        self.api=api
        self.feed_url=feed_url.strip()
        self.finam_enabled=finam_enabled
        self.session=session or requests.Session()

    def _enrich_coupons(self, records: list[dict], today: date):
        """Best effort, limited lookups; no invented yields or coupon rates."""
        from datetime import timedelta
        types = {
            'COUPON_TYPE_CONSTANT':'Постоянный', 'COUPON_TYPE_FLOATING':'Плавающий',
            'COUPON_TYPE_DISCOUNT':'Дисконтный', 'COUPON_TYPE_MORTGAGE':'Ипотечный',
            'COUPON_TYPE_FIX':'Фиксированный', 'COUPON_TYPE_VARIABLE':'Переменный',
            'COUPON_TYPE_OTHER':'Другой',
        }
        for item in records[:30]:
            uid=item.get('uid')
            if not uid:
                continue
            try:
                from_day=date.fromisoformat(item['placement_date'])
                events=self.api.coupons(uid,from_day,from_day+timedelta(days=400))
                if not events:continue
                event=min(events,key=lambda x:x.get('couponDate','9999'))
                coupon_type=types.get(event.get('couponType'))
                if coupon_type:item['coupon_type']=coupon_type
                amount,ccy=_money(event.get('payOneBond'))
                item['coupon_amount']=amount
                item['coupon_currency']=ccy
            except (TInvestError, ValueError, TypeError, KeyError):
                # An unavailable calendar must never block the discovery run.
                continue

    def scan(self, today: date | None = None) -> dict:
        today=today or datetime.now(self.api.tz).date()
        if not self._lock.acquire(blocking=False):
            return {'busy':True,'sources':[]}
        try:
            outcomes=[]
            try:
                response=self.api.call('InstrumentsService','Bonds',{'instrumentStatus':'INSTRUMENT_STATUS_ALL'})
                bonds=response.get('instruments')
                if not isinstance(bonds,list):
                    raise TInvestError('Bonds: отсутствует список instruments')
                records=[]
                for raw in bonds:
                    item=from_tinvest(raw,today)
                    if item: records.append(item)
                self._enrich_coupons(records,today)
                outcomes.append(self.store.save('tinvest',records))
            except Exception as exc:
                self.store.failure('tinvest',f'{type(exc).__name__}: {exc}')
                outcomes.append({'source':'tinvest','error':type(exc).__name__})
            if self.finam_enabled:
                try:
                    url='https://www.finam.ru/calendar/bonds/ibo/'
                    res=self.session.get(url,timeout=20,headers={'Accept':'text/html', 'User-Agent':'Mozilla/5.0 (compatible; PersonalPlacementCalendar/1.0)'})
                    res.raise_for_status()
                    outcomes.append(self.store.save('finam',finam_calendar(res.text,today)))
                except Exception as exc:
                    self.store.failure('finam',f'{type(exc).__name__}: {exc}')
                    outcomes.append({'source':'finam','error':type(exc).__name__})
            if self.feed_url:
                try:
                    parsed=urlsplit(self.feed_url)
                    if parsed.scheme!='https' or not parsed.hostname:
                        raise ValueError('PLACEMENTS_FEED_URL должен быть HTTPS')
                    res=self.session.get(self.feed_url,timeout=20,headers={'Accept':'application/json'})
                    res.raise_for_status()
                    data=res.json()
                    if not isinstance(data,dict) or not isinstance(data.get('placements'),list):
                        raise ValueError('JSON должен содержать массив placements')
                    items=[p for raw in data['placements'] if (p:=from_feed(raw,today))]
                    outcomes.append(self.store.save('feed',items))
                except Exception as exc:
                    self.store.failure('feed',f'{type(exc).__name__}: {exc}')
                    outcomes.append({'source':'feed','error':type(exc).__name__})
            return {'busy':False,'sources':outcomes}
        finally:
            self._lock.release()
