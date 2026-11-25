"""Send already prepared VK RSS news to a separate Telegram tech channel.

Telegram deliveries are independently tracked in SQLite, preserving identical
text and preventing automatic retries after ambiguous network failures.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests

from .vk_news import VKNewsQueue


def _enabled(name: str) -> bool:
    return os.getenv(name, 'false').strip().lower() in ('true', '1', 'yes')


class TechTelegramPublisher:
    def __init__(self, token: str, channel_id: str, session=None):
        if not token or not channel_id:
            raise ValueError('Укажи TELEGRAM_BOT_TOKEN и TECH_TELEGRAM_CHANNEL_ID в .env')
        self.token, self.channel_id = token, channel_id
        self.session = session or requests.Session()

    def _check_call(self, method, payload):
        try:
            response = self.session.post(f'https://api.telegram.org/bot{self.token}/{method}',
                                         json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()
            if not data.get('ok') or not isinstance(data.get('result'), dict):
                raise ValueError
            return data['result']
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            # Never include the token-bearing request URL or the original exception.
            raise ValueError('Не удалось проверить Telegram. Проверь токен, адрес канала и права бота.') from None

    def resolve_chat(self, chat_id):
        return self._check_call('getChat', {'chat_id': chat_id})

    def check_channel(self):
        me = self._check_call('getMe', {})
        chat = self.resolve_chat(self.channel_id)
        if chat.get('type') != 'channel' or not isinstance(chat.get('id'), int) or not isinstance(me.get('id'), int):
            raise ValueError('Нужен Telegram-канал, а не личный чат, группа или username бота.')
        member = self._check_call('getChatMember', {'chat_id': chat['id'], 'user_id': me['id']})
        if member.get('status') != 'creator' and not (member.get('status') == 'administrator' and member.get('can_post_messages')):
            raise ValueError('Добавь бота в администраторы канала новостей с правом публикации сообщений.')
        self.verified_chat_id = chat['id']
        return {'id': chat['id'], 'title': chat.get('title', ''), 'username': chat.get('username', ''), 'can_post': True}

    def send(self, text: str) -> str:
        if not text.strip() or len(text) > 4096:
            raise ValueError('Текст Telegram должен содержать 1–4096 символов')
        try:
            response = self.session.post(
                f'https://api.telegram.org/bot{self.token}/sendMessage',
                json={'chat_id': self.channel_id, 'text': text, 'disable_web_page_preview': True},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError('Нет подтверждения отправки в Telegram; автоматический повтор заблокирован') from None
        if not payload.get('ok') or not isinstance(payload.get('result'), dict):
            raise RuntimeError('Telegram отклонил публикацию; проверь права бота и ID канала')
        message_id = payload['result'].get('message_id')
        if not isinstance(message_id, int):
            raise RuntimeError('Telegram не вернул message_id')
        username = self.channel_id.lstrip('@') if self.channel_id.startswith('@') else ''
        return f'https://t.me/{username}/{message_id}' if username else str(message_id)


class TelegramNewsQueue:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        VKNewsQueue(self.db_path)  # ensures VK ledger is available
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS tech_telegram_publications (
                news_id INTEGER PRIMARY KEY, status TEXT NOT NULL,
                message TEXT NOT NULL, message_url TEXT, updated_at TEXT NOT NULL
            )''')

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def pending(self, limit: int = 4):
        with self.connect() as db:
            return [dict(r) for r in db.execute('''
                SELECT v.news_id, v.message, v.post_url AS vk_url
                FROM vk_news_publications v
                LEFT JOIN tech_telegram_publications t ON t.news_id = v.news_id
                WHERE v.status = 'published' AND t.news_id IS NULL
                ORDER BY v.updated_at ASC, v.news_id ASC LIMIT ?
            ''', (max(0, min(limit, 50)),))]

    def reserve(self, news_id: int, message: str) -> bool:
        with self.connect() as db:
            return db.execute('''INSERT OR IGNORE INTO tech_telegram_publications
                (news_id,status,message,message_url,updated_at) VALUES (?, 'reserved', ?, NULL, ?)''',
                (news_id, message, datetime.now(timezone.utc).isoformat())).rowcount == 1

    def finish(self, news_id: int, status: str, url: str | None = None) -> None:
        with self.connect() as db:
            db.execute('''UPDATE tech_telegram_publications
                SET status=?,message_url=?,updated_at=? WHERE news_id=?''',
                (status, url, datetime.now(timezone.utc).isoformat(), news_id))


def sync(db_path, *, publish=False, limit=4, publisher=None):
    """Cross-post VK-published posts, without translating or republishing to VK."""
    queue = TelegramNewsQueue(db_path)
    pending = queue.pending(limit)
    if not publish:
        return {'pending': pending, 'sent': [], 'errors': []}
    if not _enabled('TECH_TG_AUTO_PUBLISH'):
        raise RuntimeError('TECH_TG_AUTO_PUBLISH=false — отправка в Telegram выключена')
    publisher = publisher or TechTelegramPublisher(
        os.getenv('TELEGRAM_BOT_TOKEN', '').strip(),
        os.getenv('TECH_TELEGRAM_CHANNEL_ID', '').strip(),
    )
    sent, errors = [], []
    for entry in pending:
        news_id = entry['news_id']
        if len(entry['message']) > 4096:
            errors.append({'news_id': news_id, 'error': 'Текст превышает лимит Telegram 4096 символов'})
            continue
        if not queue.reserve(news_id, entry['message']):
            continue
        try:
            url = publisher.send(entry['message'])
        except Exception:
            queue.finish(news_id, 'uncertain')
            errors.append({'news_id': news_id, 'error': 'Результат Telegram не подтверждён; повтор заблокирован, проверь канал вручную'})
            break
        queue.finish(news_id, 'published', url)
        sent.append({'news_id': news_id, 'url': url})
    return {'pending': len(pending), 'sent': sent, 'errors': errors}
