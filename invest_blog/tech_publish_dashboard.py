"""Read-only publishing overview and explicitly triggered dispatch for local dashboard."""
from __future__ import annotations
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .vk_news import VKNewsQueue, run as publish_vk
from .tech_telegram import TelegramNewsQueue, sync as publish_tg


def enabled(key):
    return os.getenv(key, '').lower() in ('true', '1', 'yes')


def schedule(tz):
    slots = []
    for raw in os.getenv('VK_NEWS_TIMES', '10:00,13:00,16:00,19:00').split(','):
        try:
            hour, minute = map(int, raw.strip().split(':'))
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError
            slots.append((hour, minute))
        except ValueError:
            continue
    return sorted(set(slots))


def overview(db_path, tz):
    tz = tz or ZoneInfo('Europe/Moscow')
    now = datetime.now(tz)
    vk = VKNewsQueue(db_path)
    tg = TelegramNewsQueue(db_path)
    with vk.connect() as db:
        vk_rows = [dict(row) for row in db.execute('''SELECT p.news_id, n.title, p.day, p.status, p.post_url AS url,
            p.updated_at, 'VK' AS platform FROM vk_news_publications p
            LEFT JOIN tech_news n ON n.id=p.news_id ORDER BY p.updated_at DESC LIMIT 30''')]
    with tg.connect() as db:
        tg_rows = [dict(row) for row in db.execute('''SELECT p.news_id, n.title, substr(p.updated_at,1,10) AS day,
            p.status,p.message_url AS url,p.updated_at,'Telegram' AS platform
            FROM tech_telegram_publications p LEFT JOIN tech_news n ON n.id=p.news_id
            ORDER BY p.updated_at DESC LIMIT 30''')]
    items = sorted(vk_rows+tg_rows, key=lambda x: x['updated_at'], reverse=True)[:35]
    next_time = None
    if enabled('VK_AUTO_PUBLISH'):
        for h,m in schedule(tz):
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            if next_time is None or candidate < next_time:
                next_time = candidate
    today = now.date().isoformat()
    return {'vk_enabled':enabled('VK_AUTO_PUBLISH'), 'telegram_enabled':enabled('TECH_TG_AUTO_PUBLISH'),
            'telegram_configured':bool(os.getenv('TECH_TELEGRAM_CHANNEL_ID')),
            'times':[f'{h:02d}:{m:02d}' for h,m in schedule(tz)],
            'timezone':str(tz),'next_at':next_time.isoformat() if next_time else None,
            'today':{'vk':sum(x['day']==today and x['status']=='published' for x in vk_rows),
                     'telegram':sum(datetime.fromisoformat(x['updated_at']).astimezone(tz).date().isoformat()==today and x['status']=='published' for x in tg_rows)},
            'vk_daily_reserved':vk.count(today),'tg_pending':len(tg.pending(50)), 'history':items}


def dispatch(db_path, tz, *, limit=1):
    if not enabled('VK_AUTO_PUBLISH'):
        raise ValueError('VK_AUTO_PUBLISH=false: сначала включи публикацию в .env и перезапусти приложение')
    result = {'vk':None,'telegram':None}
    try:
        result['vk'] = publish_vk(db_path, tz, publish=True, limit=limit)
    except Exception as exc:
        result['vk'] = {'errors':[{'error':str(exc)}]}
    # Telegram independently retries confirmed VK deliveries, even if VK has no new post.
    if enabled('TECH_TG_AUTO_PUBLISH'):
        try:
            result['telegram'] = publish_tg(db_path, publish=True, limit=limit)
        except Exception as exc:
            result['telegram'] = {'errors':[{'error':str(exc)}]}
    return result
