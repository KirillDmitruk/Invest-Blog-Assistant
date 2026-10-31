"""Curated read-only RSS/Atom collector for Tech & Gadgets. No auto publishing."""
from __future__ import annotations

import hashlib
import html
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

log = logging.getLogger(__name__)
ATOM = '{http://www.w3.org/2005/Atom}'
CATEGORIES = ('tech', 'ai', 'market', 'robotics', 'gadgets', 'diy', 'printing', 'space')
CATEGORY_LABELS = {'tech':'Технологии', 'ai':'AI', 'market':'Рынок и IPO',
                   'robotics':'Робототехника','gadgets':'Гаджеты и прототипы',
                   'diy':'DIY и электроника','printing':'3D-печать','space':'Космос'}

# Fixed allowlist of feeds; no user-controlled HTTP target or open proxy.
FEEDS = (
    ('The Verge', 'https://www.theverge.com/rss/index.xml'),
    ('Ars Technica', 'https://feeds.arstechnica.com/arstechnica/index'),
    ('TechCrunch', 'https://techcrunch.com/feed/'),
    ('Engadget', 'https://www.engadget.com/rss.xml'),
    ('IEEE Spectrum', 'https://spectrum.ieee.org/feed'),
    ('MIT Technology Review', 'https://www.technologyreview.com/feed/'),
    ('NASA', 'https://www.nasa.gov/feed/'),
)
KEYWORDS = {
    'ai': r'\b(ai|artificial intelligence|machine learning|llm|openai|chatgpt|gemini|anthropic|neural|генеративн)\b',
    'market': r'\b(ipo|stock market|nasdaq|earnings|shares|stocks|valuation|funding|investors|acquisition|merger|wall street|market cap|бирж|акци[йи]|рынок)\b',
    'robotics': r'\b(robot|robotics|humanoid|automation|actuator|quadruped|робот)\b',
    'gadgets': r'\b(gadget|device|prototype|smartphone|wearable|headset|smartwatch|chip|laptop|hardware|гаджет|прототип)\b',
    'diy': r'\b(diy|arduino|raspberry pi|esp32|microcontroller|maker|electronics|microchip|электрон)\b',
    'printing': r'\b(3d.print(?:ing)?|additive manufacturing|filament|resin printer|3d-печать)\b',
    'space': r'\b(space|nasa|spacex|rocket|satellite|moon|mars|orbital|launch|космос|спутник)\b',
}


def clean_text(value: str, max_length: int = 900) -> str:
    value = re.sub(r'<[^>]+>', ' ', value or '')
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()[:max_length]


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != 'https' or not parts.netloc or parts.username or parts.password:
        return ''
    query = urlencode([(k,v) for k,v in parse_qsl(parts.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')])
    return urlunsplit(('https',parts.netloc.lower(),parts.path.rstrip('/') or '/',query,''))


def parse_date(value: str) -> str | None:
    if not value: return None
    try:
        date = datetime.fromisoformat(value.replace('Z','+00:00')) if re.match(r'^\d{4}-\d{2}-\d{2}T', value) else parsedate_to_datetime(value)
        if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def category_for(title: str, summary: str, source: str) -> str:
    text = (title + ' ' + summary[:350]).lower()
    # Explicit keyword categories have precedence over generic source labels.
    for category in ('printing','robotics','space','diy','ai','market','gadgets'):
        if re.search(KEYWORDS[category], text, re.I): return category
    return 'tech'


def parse_feed(content: bytes, source: str) -> list[dict]:
    if len(content)>2_000_000: raise ValueError('RSS превышает лимит 2 МБ')
    root=ET.fromstring(content)
    items=root.findall('./channel/item') if root.tag.lower() in ('rss','rdf') else []
    if not items: items=root.findall('.//{http://www.w3.org/2005/Atom}entry')
    if not items: items=root.findall('.//{http://purl.org/rss/1.0/}item')
    results=[]
    for entry in items[:100]:
        atom=entry.tag == ATOM+'entry'
        get=lambda name: (entry.findtext(ATOM+name if atom else name) or '').strip()
        title=clean_text(get('title'),250)
        link=''
        if atom:
            for l in entry.findall(ATOM+'link'):
                if l.get('rel','alternate')=='alternate' and l.get('href'):
                    link=l.get('href',''); break
        else: link=get('link')
        link=canonical_url(link)
        if not title or not link: continue
        summary=clean_text(get('summary') or get('description') or get('content'),900)
        published=parse_date(get('published') or get('updated') or get('pubDate') or get('{http://purl.org/dc/elements/1.1/}date'))
        results.append({'source':source,'title':title,'url':link,'summary':summary,
                        'published_at':published,'category':category_for(title,summary,source)})
    return results


class TechNewsStore:
    def __init__(self,path:Path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tech_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE, summary TEXT NOT NULL, category TEXT NOT NULL,
                published_at TEXT, discovered_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new')''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_tech_news_date ON tech_news(discovered_at DESC)''')
            db.execute('''CREATE TABLE IF NOT EXISTS tech_news_sources (
                source TEXT PRIMARY KEY, checked_at TEXT NOT NULL, status TEXT NOT NULL,
                found INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0, error TEXT)''')
    def _db(self):
        db=sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        return db
    def save(self,items:list[dict]) -> int:
        now=datetime.now(timezone.utc).isoformat()
        added=0
        with self._db() as db:
            for item in items:
                cur=db.execute('''INSERT OR IGNORE INTO tech_news(source,title,url,summary,category,published_at,discovered_at)
                    VALUES (?,?,?,?,?,?,?)''',(item['source'],item['title'],item['url'],item['summary'],item['category'],item['published_at'],now))
                added+=cur.rowcount
        return added
    def record_source(self,source,status,found=0,added=0,error=None):
        with self._db() as db:
            db.execute('''INSERT INTO tech_news_sources VALUES (?,?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status,
               found=excluded.found,added=excluded.added,error=excluded.error''',
               (source,datetime.now(timezone.utc).isoformat(),status,found,added,str(error)[:400] if error else None))
    def sources(self):
        with self._db() as db:
            rows={r['source']:dict(r) for r in db.execute('SELECT * FROM tech_news_sources')}
        return [rows.get(name,{'source':name,'status':'pending','checked_at':None,'found':0,'added':0,'error':None}) for name,_ in FEEDS]
    def list_news(self,category:str|None=None,status:str|None=None,limit:int=100):
        if category and category not in CATEGORIES: raise ValueError('Неизвестная категория')
        if status and status not in ('new','saved','dismissed'): raise ValueError('Неизвестный статус')
        query='SELECT * FROM tech_news WHERE 1=1'; params=[]
        if category: query+=' AND category=?';params.append(category)
        if status: query+=' AND status=?';params.append(status)
        query+=' ORDER BY COALESCE(published_at,discovered_at) DESC, id DESC LIMIT ?'; params.append(min(max(int(limit),1),200))
        with self._db() as db:
            return [dict(r) for r in db.execute(query,params)]
    def get(self,news_id:int):
        with self._db() as db:
            row=db.execute('SELECT * FROM tech_news WHERE id=?',(news_id,)).fetchone()
            return dict(row) if row else None
    def mark(self,news_id:int,status:str):
        if status not in ('new','saved','dismissed'): raise ValueError('Неизвестный статус')
        with self._db() as db:
            return db.execute('UPDATE tech_news SET status=? WHERE id=?',(status,news_id)).rowcount==1
    def stats(self):
        with self._db() as db:
            return dict(db.execute("SELECT count(*) total,count(CASE WHEN status='new' THEN 1 END) new FROM tech_news").fetchone())


class TechNewsCollector:
    def __init__(self,store:TechNewsStore,session=None):
        self.store=store
        self.session=session or requests.Session()
    def scan(self):
        report=[]
        for source,url in FEEDS:
            try:
                response=self.session.get(url,timeout=(5,15),headers={'User-Agent':'ContentHub-RSS-Reader/2.0 (+personal news dashboard)'},allow_redirects=True)
                response.raise_for_status()
                if len(response.content)>2_000_000: raise ValueError('Слишком большой ответ')
                items=parse_feed(response.content,source)
                added=self.store.save(items)
                self.store.record_source(source,'ok',len(items),added)
                report.append({'source':source,'status':'ok','found':len(items),'added':added})
            except (requests.RequestException,ValueError,ET.ParseError) as exc:
                log.warning('Tech RSS %s: %s',source,exc)
                self.store.record_source(source,'error',error=exc)
                report.append({'source':source,'status':'error','error':str(exc)[:200]})
        return report
