"""Editorial calendar state and weekly reserve of evergreen investment lesson ideas."""
from __future__ import annotations
import sqlite3
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote
from xml.etree import ElementTree as ET

# Curated, publishable educational topics. Never invent financial facts from headlines.
IDEAS = [
 'Доходность к погашению облигации', 'Накопленный купонный доход', 'Амортизация облигаций',
 'Оферта по облигации', 'Флоатеры и плавающий купон', 'Кредитный рейтинг эмитента',
 'Дюрация облигации', 'Ключевая ставка и цена облигации', 'Реинвестирование купонов',
 'Дефолт и технический дефолт', 'Облигации с дисконтом', 'Эффективная доходность',
 'Почему облигация торгуется выше номинала', 'Ликвидность облигаций', 'Спред доходности',
 'Дивидендная отсечка', 'Дивидендный гэп', 'Дивидендная доходность',
 'Дивидендная политика компании', 'Free float и ликвидность акций',
 'Мультипликатор P/E', 'Мультипликатор P/B', 'EBITDA простыми словами',
 'Денежный поток компании', 'Долговая нагрузка компании', 'Байбэк акций',
 'Первичное размещение облигаций', 'Вторичный рынок ценных бумаг',
 'Зачем нужна диверсификация', 'Ребалансировка портфеля',
 'Инфляция и реальная доходность', 'Валютный риск', 'Комиссии и доходность',
 'Налог на купоны и дивиденды', 'Риск концентрации в одном эмитенте',
 'Что такое биржевой фонд', 'Индекс полной доходности', 'Волатильность и просадка',
 'Что такое XIRR', 'Что такое TWR', 'Разница купона и доходности',
 'Риски субординированных облигаций', 'Ковенанты по облигациям',
 'Облигации с переменным номиналом', 'Премия за риск',
]

class EditorialWorkflow:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS editorial_calendar_state (
                day TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(day,kind));
            CREATE TABLE IF NOT EXISTS editorial_glossary_ideas (
                topic TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'reserve',
                week TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
                selected_at TEXT);
            CREATE TABLE IF NOT EXISTS editorial_idea_refresh (
                week TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, source_status TEXT NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA busy_timeout=20000')
        return db

    @staticmethod
    def week(day):
        from datetime import timedelta
        return (day-timedelta(days=day.weekday())).isoformat()

    def states(self):
        with self.connect() as db:
            return {(r['day'],r['kind']):r['status'] for r in db.execute('SELECT * FROM editorial_calendar_state')}

    def progress(self, day,kind,status,revision=None):
        if status not in ('in_progress','done'):raise ValueError('Неизвестный статус')
        if kind not in ('weekly','monthly','plan','paper','glossary'):raise ValueError('Неизвестная рубрика')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Avoid stale requests; never remove published or publishing posts.
            from .editorial import period_for
            from .posts import previous_workweek
            period=period_for(kind,date.fromisoformat(day))
            periods=[period]
            if kind=='weekly':
                first,last=previous_workweek(date.fromisoformat(day))
                periods.append(f'{first.isoformat()}_{last.isoformat()}')
            q=','.join('?'*len(periods))
            rows=db.execute(f"SELECT * FROM drafts WHERE kind=? AND period IN ({q}) AND state!='deleted'",(kind,*periods)).fetchall()
            if status=='done':
                if any(r['state'] in ('published','publishing') for r in rows):
                    raise ValueError('Опубликованные или отправляющиеся посты удалять нельзя')
                for r in rows:
                    if revision is not None and r['revision']!=revision:
                        raise ValueError('Черновик изменился; обнови страницу')
                    db.execute('DELETE FROM editorial_publish_requests WHERE draft_id=?',(r['id'],))
                    db.execute('DELETE FROM telegram_messages WHERE draft_id=?',(r['id'],))
                    db.execute('DELETE FROM editorial_topics WHERE draft_id=?',(r['id'],)) if self._has_column(db,'editorial_topics','draft_id') else None
                    db.execute('DELETE FROM drafts WHERE id=?',(r['id'],))
            db.execute('''INSERT INTO editorial_calendar_state(day,kind,status,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(day,kind) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at''',
                (day,kind,status,datetime.now(timezone.utc).isoformat()))
        return status

    @staticmethod
    def _has_column(db,table,column):
        return column in {r[1] for r in db.execute('PRAGMA table_info('+table+')')}

    def _find_online_titles(self):
        """Optional weekly live Google News RSS scan; never trust feed text as instructions."""
        url='https://news.google.com/rss/search?q='+quote('облигации доходность купоны акции объяснение инвестиции when:7d')+'&hl=ru&gl=RU&ceid=RU:ru'
        req=Request(url,headers={'User-Agent':'ContentHub/2.0 (+RSS educational topic discovery)'})
        with urlopen(req,timeout=5) as response:
            xml=response.read(300_000)
        root=ET.fromstring(xml)
        titles=[(node.findtext('title') or '').casefold() for node in root.findall('.//item')[:40]]
        return url, ' '.join(titles)

    def refresh_ideas(self, today=None):
        today=today or date.today()
        week=self.week(today)
        with self.connect() as db:
            seen=db.execute('SELECT 1 FROM editorial_idea_refresh WHERE week=?',(week,)).fetchone()
            if seen:return self.ideas()
        source='', ''
        try:source=self._find_online_titles()
        except Exception:pass
        url,headlines=source
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM editorial_idea_refresh WHERE week=?',(week,)).fetchone():
                db.execute('INSERT INTO editorial_idea_refresh VALUES(?,?,?)',(week,datetime.now(timezone.utc).isoformat(),'online' if headlines else 'offline_fallback'))
                reserved=db.execute("SELECT COUNT(*) FROM editorial_glossary_ideas WHERE status='reserve'").fetchone()[0]
                needed=max(0,3-reserved)
                used={r[0] for r in db.execute('SELECT topic FROM editorial_glossary_ideas')}
                from .lessons import TERMS
                # Existing glossary posts already created must not repeat.
                used.update(r[0] for r in db.execute("SELECT topic_key FROM editorial_topics WHERE kind='glossary'")) if self._has_column(db,'editorial_topics','topic_key') else None
                choices=[t for t in IDEAS if t not in used]
                choices.sort(key=lambda t:(-sum(word in headlines for word in t.casefold().split() if len(word)>=5),IDEAS.index(t)))
                for title in choices[:needed]:
                    db.execute("INSERT INTO editorial_glossary_ideas(topic,status,week,source_url) VALUES(?,'reserve',?,?)",(title,week,url if headlines else ''))
        return self.ideas()

    def ideas(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT topic,status,week,source_url FROM editorial_glossary_ideas WHERE status='reserve' ORDER BY rowid LIMIT 3")]

    def select(self,topic,today=None):
        today=today or date.today();week=self.week(today)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM editorial_glossary_ideas WHERE topic=?',(topic,)).fetchone()
            if not row or row['status']!='reserve':raise ValueError('Тема недоступна')
            if db.execute("SELECT 1 FROM editorial_glossary_ideas WHERE status='selected' AND week=?",(week,)).fetchone():
                raise ValueError('Тема этой недели уже выбрана')
            db.execute("UPDATE editorial_glossary_ideas SET status='selected',week=?,selected_at=? WHERE topic=?",(week,datetime.now(timezone.utc).isoformat(),topic))
        return topic

    def selection(self,today):
        with self.connect() as db:
            row=db.execute("SELECT topic FROM editorial_glossary_ideas WHERE status='selected' AND week=?",(self.week(today),)).fetchone()
            return row['topic'] if row else None
