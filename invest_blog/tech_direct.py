"""Independent RSS -> Telegram publishing, once per half-hour slot.

Reservations are durable and atomic across processes. An ambiguous send never
automatically retries, because Telegram sendMessage has no idempotency key.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import sqlite3

from .tech_telegram import TelegramNewsQueue, TechTelegramPublisher, _enabled
from .vk_news import LocalTranslator, compose_investment as compose


def direct_mode():
    return os.getenv('TECH_TG_MODE', 'crosspost').strip().lower() == 'direct'


@dataclass(frozen=True)
class DirectSchedule:
    start_hour: int = 9
    end_hour: int = 23
    daily_limit: int = 48
    max_age_hours: int = 24

    @classmethod
    def from_env(cls):
        try:
            obj = cls(*(int(os.getenv(key, str(default))) for key, default in (
                ('TECH_TG_START_HOUR', 9), ('TECH_TG_END_HOUR', 23),
                ('TECH_TG_DAILY_LIMIT', 48), ('TECH_TG_MAX_AGE_HOURS', 24))))
        except ValueError as exc:
            raise ValueError('Часы и лимиты Tech Telegram должны быть целыми числами') from exc
        if not 0 <= obj.start_hour < obj.end_hour <= 24:
            raise ValueError('Часы Tech Telegram: 0 <= начало < окончание <= 24')
        if not 1 <= obj.daily_limit <= 48 or not 1 <= obj.max_age_hours <= 72:
            raise ValueError('Лимит Telegram: 1–48 в день; возраст новостей: 1–72 часа')
        return obj

    def slots(self):
        return [(h, m) for h in range(self.start_hour, self.end_hour) for m in (0, 30)]

    def next_at(self, clock):
        for days in (0, 1):
            for hour, minute in self.slots():
                candidate = (clock + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate > clock:
                    return candidate


class DirectQueue(TelegramNewsQueue):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tech_tg_direct_claims (
                    channel_id TEXT NOT NULL, slot INTEGER NOT NULL, day TEXT NOT NULL,
                    news_id INTEGER NOT NULL UNIQUE, title_key TEXT NOT NULL UNIQUE,
                    attempted_at TEXT NOT NULL, PRIMARY KEY(channel_id,slot)
                );
                CREATE TABLE IF NOT EXISTS tech_tg_direct_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL,
                    status TEXT NOT NULL, note TEXT NOT NULL
                );
            ''')

    def candidates(self, clock, max_age_hours):
        cutoff = (clock - timedelta(hours=max_age_hours)).astimezone(timezone.utc).isoformat()
        upper = clock.astimezone(timezone.utc).isoformat()
        with self.connect() as db:
            return [dict(r) for r in db.execute('''SELECT n.* FROM tech_news n
                LEFT JOIN tech_telegram_publications t ON t.news_id=n.id
                WHERE n.status='new' AND t.news_id IS NULL
                AND n.category IN ('russia','world','crypto')
                AND COALESCE(n.published_at,n.discovered_at) BETWEEN ? AND ?
                ORDER BY COALESCE(n.published_at,n.discovered_at) DESC,n.id DESC LIMIT 60''', (cutoff, upper))]

    def claim(self, news, message, channel, clock, daily_limit):
        slot = int(clock.timestamp()) // 1800
        title = re.sub(r'\W+', ' ', news['title'].casefold()).strip()
        title_key = hashlib.sha256(title.encode()).hexdigest()
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                used = db.execute('SELECT COUNT(*) FROM tech_tg_direct_claims WHERE channel_id=? AND day=?',
                                  (channel, clock.date().isoformat())).fetchone()[0]
                if used >= daily_limit:
                    return False
                fresh = db.execute("SELECT status FROM tech_news WHERE id=?", (news['id'],)).fetchone()
                if not fresh or fresh['status'] != 'new':
                    return False
                db.execute('''INSERT INTO tech_tg_direct_claims
                    (channel_id,slot,day,news_id,title_key,attempted_at) VALUES(?,?,?,?,?,?)''',
                           (channel, slot, clock.date().isoformat(), news['id'], title_key, clock.isoformat()))
                db.execute('''INSERT INTO tech_telegram_publications
                    (news_id,status,message,message_url,updated_at) VALUES(?,'reserved',?,NULL,?)''',
                           (news['id'], message, clock.astimezone(timezone.utc).isoformat()))
                return True
        except sqlite3.IntegrityError:
            return False

    def record(self, status, note, clock):
        with self.connect() as db:
            db.execute('INSERT INTO tech_tg_direct_runs(checked_at,status,note) VALUES(?,?,?)',
                       (clock.isoformat(), status, note[:1000]))

    def last_run(self):
        with self.connect() as db:
            row = db.execute('SELECT checked_at,status,note FROM tech_tg_direct_runs ORDER BY id DESC LIMIT 1').fetchone()
            return dict(row) if row else None

    def category_counts(self, day):
        with self.connect() as db:
            return {r['category']:r['amount'] for r in db.execute('''SELECT n.category,COUNT(*) amount
                FROM tech_tg_direct_claims c JOIN tech_news n ON n.id=c.news_id WHERE c.day=? GROUP BY n.category''',(day,))}


def run(db_path, tz, *, publish=False, translator=None, publisher=None, clock=None):
    clock = clock or datetime.now(tz)
    if clock.tzinfo is None:
        raise ValueError('Для расписания нужен часовой пояс')
    config = DirectSchedule.from_env()
    queue = DirectQueue(db_path)
    from .tech_news import TechNewsStore
    controls = TechNewsStore(db_path).controls()
    result = {'prepared': [], 'sent': [], 'errors': []}

    def finish(status, note):
        result['note'] = note
        if publish:
            queue.record(status, note, clock)
        return result

    if publish:
        if not controls['enabled']:
            return finish('paused', 'Автопубликация на паузе в хабе.')
        if not direct_mode() or not _enabled('TECH_TG_AUTO_PUBLISH'):
            raise ValueError('Прямая публикация Telegram выключена. Нужны TECH_TG_MODE=direct и TECH_TG_AUTO_PUBLISH=true.')
        if not config.start_hour <= clock.hour < config.end_hour:
            return finish('skipped', 'Сейчас вне часов публикации.')
    candidates = queue.candidates(clock, config.max_age_hours)
    if not candidates:
        return finish('skipped', 'Свежих неопубликованных новостей нет; слот пропущен.')
    cached_translator = [translator]
    def translate(text):
        if cached_translator[0] is None:
            cached_translator[0] = LocalTranslator()
        return cached_translator[0](text)
    counts=queue.category_counts(clock.date().isoformat())
    candidates.sort(key=lambda news:counts.get(news['category'],0))
    for news in candidates:
        if news['category'] not in controls['categories']:
            continue
        try:
            text = compose(news, translate)
        except RuntimeError:
            result['errors'].append({'error':'Локальный перевод EN→RU недоступен; зарубежная новость пропущена.'})
            continue
        except ValueError:
            continue  # No empty summaries, failed translations or invented fallback text.
        if not text.strip() or len(text) > 4096:
            continue
        if not publish:
            result['prepared'].append({'news_id': news['id'], 'text': text})
            return finish('preview', 'Предпросмотр одной новости. Отправки нет.')
        fresh_controls=TechNewsStore(db_path).controls()
        if not fresh_controls['enabled'] or news['category'] not in fresh_controls['categories']:
            return finish('paused', 'Настройки изменились во время подготовки; отправка пропущена.')
        if publisher is None:
            publisher = TechTelegramPublisher(os.getenv('TELEGRAM_BOT_TOKEN', '').strip(),
                                               os.getenv('TECH_TELEGRAM_CHANNEL_ID', '').strip())
            channel_info = publisher.check_channel()
            investment = os.getenv('TELEGRAM_CHANNEL_ID', '').strip()
        channel = str(getattr(publisher, 'verified_chat_id', publisher.channel_id))
        if not queue.claim(news, text, channel, clock, config.daily_limit):
            continue
        try:
            url = publisher.send(text)
        except Exception:
            queue.finish(news['id'], 'uncertain')
            result['errors'].append({'news_id': news['id'], 'error': 'Telegram не подтвердил отправку. Повтор заблокирован; проверь канал вручную.'})
            return finish('uncertain', 'Результат отправки не подтверждён. Автоматического повтора не будет.')
        queue.finish(news['id'], 'published', url)
        result['sent'].append({'news_id': news['id'], 'url': url})
        return finish('published', 'Одна новость опубликована в Telegram.')
    if result['errors']:
        return finish('error', 'Не удалось подготовить перевод. Русские новости продолжат выходить без переводчика.')
    return finish('skipped', 'Слот занят, лимит исчерпан либо подходящих новостей нет.')


def publish_cycle(db_path, tz):
    """Refresh sources before the slot; old cached news still obey the age limit."""
    from .tech_news import TechNewsCollector, TechNewsStore
    import logging
    log = logging.getLogger(__name__)
    try:
        TechNewsCollector(TechNewsStore(db_path)).scan()
    except Exception:
        log.warning('Не удалось обновить RSS; проверяем свежую сохранённую очередь.')
    try:
        result = run(db_path, tz, publish=True)
        log.info('%s', result.get('note'))
        return result
    except Exception as exc:
        note = 'Публикация не выполнена: проверь канал и переводчик (' + type(exc).__name__ + ')'
        DirectQueue(db_path).record('error', note, datetime.now(tz))
        log.warning('%s', note)
        return {'sent': [], 'errors': [{'error': note}], 'note': note}


def serve(db_path, tz):
    """Standalone worker: no brokerage sync, polling or private investment posts."""
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    import logging
    if not direct_mode() or not _enabled('TECH_TG_AUTO_PUBLISH'):
        raise ValueError('Сначала включи TECH_TG_MODE=direct и TECH_TG_AUTO_PUBLISH=true в .env')
    config = DirectSchedule.from_env()
    info = TechTelegramPublisher(os.getenv('TELEGRAM_BOT_TOKEN', '').strip(),
                                 os.getenv('TECH_TELEGRAM_CHANNEL_ID', '').strip()).check_channel()
    scheduler = BlockingScheduler(timezone=tz)
    scheduler.add_job(publish_cycle, CronTrigger(
        hour=','.join(str(h) for h in range(config.start_hour, config.end_hour)), minute='0,30', timezone=tz),
        args=[db_path, tz], id='tech-telegram-direct', max_instances=1, coalesce=True, misfire_grace_time=120)
    logging.getLogger(__name__).info('Инвестиционные новости: %s; следующий слот %s',
                                     info['title'], config.next_at(datetime.now(tz)).isoformat())
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        if scheduler.running:
            scheduler.shutdown(wait=False)
