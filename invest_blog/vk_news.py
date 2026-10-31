"""RSS -> offline translation -> VK wall, with durable delivery ledger."""
from __future__ import annotations
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .tech_news import TechNewsStore, clean_text
from .vk_publisher import VKConfig, VKPublisher

class LocalTranslator:
    def __init__(self):
        try:
            import argostranslate.translate as translate
        except ImportError as exc:
            raise RuntimeError('Установи argostranslate и модель en-ru (VK_AUTO_SETUP.md)') from exc
        languages = translate.get_installed_languages()
        src = next((x for x in languages if x.code == 'en'), None)
        dst = next((x for x in languages if x.code == 'ru'), None)
        self.engine = src.get_translation(dst) if src and dst else None
        if self.engine is None:
            raise RuntimeError('Не найдена локальная модель перевода en-ru')
    def __call__(self, text):
        return self.engine.translate(text)

def compose(news, translator):
    title = clean_text(news['title'], 250)
    summary = clean_text(news.get('summary') or '', 600)
    if len(summary) < 60:
        raise ValueError('Мало фактов для поста')
    translated_title = clean_text(translator(title), 300)
    translated_summary = clean_text(translator(summary), 800)
    if len(translated_title) < 12 or len(translated_summary) < 35 or translated_title.casefold() == title.casefold() or translated_summary.casefold() == summary.casefold():
        raise ValueError('Некорректный или неполный перевод')
    return f'{translated_title}\n\n{translated_summary}\n\nИсточник: {news["source"]}\n{news["url"]}'

class VKNewsQueue:
    def __init__(self, path):
        self.path = Path(path)
        TechNewsStore(self.path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS vk_news_publications (
               news_id INTEGER PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL,
               message TEXT NOT NULL, post_url TEXT, updated_at TEXT NOT NULL)''')
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db
    def count(self, day):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM vk_news_publications WHERE day=? AND status IN ('reserved','uncertain','published')", (day,)).fetchone()[0]
    def candidates(self):
        cutoff = (datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with self.connect() as db:
            return [dict(x) for x in db.execute('''SELECT n.* FROM tech_news n LEFT JOIN vk_news_publications p ON n.id=p.news_id
              WHERE n.status='new' AND p.news_id IS NULL AND COALESCE(n.published_at,n.discovered_at)>=?
              ORDER BY COALESCE(n.published_at,n.discovered_at) DESC LIMIT 60''',(cutoff,))]
    def reserve(self, news, day, message):
        with self.connect() as db:
            if db.execute("SELECT COUNT(*) FROM vk_news_publications WHERE day=? AND status IN ('reserved','uncertain','published')",(day,)).fetchone()[0]>=4:
                return False
            return db.execute("INSERT OR IGNORE INTO vk_news_publications VALUES (?,?, 'reserved',?,NULL,?)",
                              (news['id'],day,message,datetime.now(timezone.utc).isoformat())).rowcount==1
    def finish(self, news_id, status, url=None):
        with self.connect() as db:
            db.execute('UPDATE vk_news_publications SET status=?,post_url=?,updated_at=? WHERE news_id=?',
                       (status,url,datetime.now(timezone.utc).isoformat(),news_id))

def run(db_path, tz, *, publish=False, limit=1, translator=None, publisher=None):
    queue = VKNewsQueue(db_path)
    day = datetime.now(tz).date().isoformat()
    capacity = max(0,min(4,limit,4-queue.count(day)))
    if not capacity:
        return {'day':day,'prepared':[],'published':[],'errors':[],'note':'Лимит 4 записи в день достигнут'}
    translator = translator or LocalTranslator()
    prepared=[]; errors=[]
    for news in queue.candidates():
        try:
            msg=compose(news,translator)
            prepared.append((news,msg))
            if len(prepared)>=capacity: break
        except ValueError as exc:
            errors.append({'news_id':news['id'],'error':str(exc)})
    if not publish:
        return {'day':day,'prepared':[{'news_id':n['id'],'text':m} for n,m in prepared], 'published':[],'errors':errors}
    cfg = VKConfig.from_env()
    if not cfg.auto_publish:
        raise RuntimeError('VK_AUTO_PUBLISH=false — публикация выключена')
    publisher = publisher or VKPublisher(cfg)
    results=[]
    for news,msg in prepared:
        if not queue.reserve(news,day,msg): continue
        guid='ch-news-'+hashlib.sha256(news['url'].encode()).hexdigest()[:36]
        try:
            url=publisher.publish(msg,guid=guid)
        except Exception:
            queue.finish(news['id'],'uncertain')
            errors.append({'news_id':news['id'],'error':'Неизвестен результат отправки: проверь стену VK вручную. Повтор заблокирован.'})
            break
        queue.finish(news['id'],'published',url)
        results.append({'news_id':news['id'],'url':url})
    return {'day':day,'prepared':len(prepared),'published':results,'errors':errors}
