"""Scheduled public Telegram news. No portfolio, trading or private wallet access."""
import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .cloud_pulse import PrivateState
from .cloud_signals import json_call
from .news_editor import generate, compose
from .tech_news import FEEDS, parse_feed

CHANNEL = '@kirintechnews'
TZ = ZoneInfo('Europe/Moscow')


class NewsState(PrivateState):
    branch = 'automation/telegram-news-state'
    filename = 'news-state.enc'

    def status(self, result, sources):
        state = self.load()
        rows = list(state['deliveries'].values())
        sent = sorted((r for r in rows if r['status'] == 'sent'), key=lambda r: r['created_at'])
        now = datetime.now(timezone.utc)
        payload = {'checked_at': now.isoformat(), 'result': result['status'], 'channel': CHANNEL,
                   'today_sent': sum(r['day'] == now.astimezone(TZ).date().isoformat() for r in sent),
                   'last_post': ({k: sent[-1].get(k) for k in ('headline', 'message_url', 'created_at')} if sent else None),
                   'sources': sources}
        path = self.url + '/contents/news-status.json'
        reply = self.session.get(path, headers=self.headers, params={'ref': self.branch}, timeout=20, allow_redirects=False)
        if reply.status_code not in (200, 404):
            raise ValueError('Не удалось обновить публичный статус')
        body = {'message': 'Update public news publishing status', 'branch': self.branch,
                'content': base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()}
        if reply.status_code == 200:
            body['sha'] = reply.json()['sha']
        json_call(self.session, 'PUT', path, headers=self.headers, json=body)


def slot(clock):
    local = clock.astimezone(TZ)
    return str(int(clock.timestamp()) // 1800) if 9 <= local.hour < 23 else None


def fresh(items, clock):
    out = []
    for item in items:
        try:
            stamp = datetime.fromisoformat(item['published_at'])
            if stamp.tzinfo is None or not timedelta(0) <= clock - stamp <= timedelta(hours=24):
                continue
            if item['category'] not in ('russia', 'world', 'crypto') or len(item['summary']) < 60:
                continue
            out.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fingerprint(item):
    # A normalized headline protects against identical stories syndicated at other URLs.
    import re
    return hashlib.sha256(re.sub(r'\W+', ' ', item['title'].casefold()).strip().encode()).hexdigest()


def collect(session, clock):
    items, sources = [], []
    for name, url in FEEDS:
        try:
            with session.get(url, timeout=(5, 15), stream=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError('RSS too large')
                    chunks.append(chunk)
            found = parse_feed(b''.join(chunks), name)
            items.extend(found)
            sources.append({'source': name, 'ok': True})
        except Exception:
            sources.append({'source': name, 'ok': False})
    return fresh(items, clock), sources


class ChannelSender:
    def __init__(self, token, session=None):
        from html import escape
        self.escape = escape
        self.session = session or requests.Session()
        self.url = 'https://api.telegram.org/bot' + token + '/'
        me = json_call(self.session, 'POST', self.url + 'getMe', json={})
        chat = json_call(self.session, 'POST', self.url + 'getChat', json={'chat_id': CHANNEL})
        result = chat.get('result', {})
        if not me.get('ok') or not chat.get('ok') or result.get('type') != 'channel' or result.get('username', '').lower() != CHANNEL[1:]:
            raise ValueError('Канал не подтверждён')
        member = json_call(self.session, 'POST', self.url + 'getChatMember', json={'chat_id': result['id'], 'user_id': me['result']['id']})
        rights = member.get('result', {})
        if not member.get('ok') or not (rights.get('status') == 'creator' or rights.get('status') == 'administrator' and rights.get('can_post_messages')):
            raise ValueError('Право публикации не подтверждено')
        self.chat = result['id']

    def send(self, text):
        lines = text.split('\n')
        html = '\n'.join('<b>' + self.escape(line) + '</b>' if i == 2 else self.escape(line) for i, line in enumerate(lines))
        result = json_call(self.session, 'POST', self.url + 'sendMessage', json={
            'chat_id': self.chat, 'text': html, 'parse_mode': 'HTML', 'disable_web_page_preview': True})
        if not result.get('ok') or type(result.get('result', {}).get('message_id')) is not int:
            raise ValueError('Отправка не подтверждена')
        return 'https://t.me/' + CHANNEL[1:] + '/' + str(result['result']['message_id'])


def publish(store, sender, items, *, editor=generate, now=None, send=False):
    clock = now or datetime.now(timezone.utc)
    key = slot(clock)
    if key is None:
        return {'status': 'outside_hours'}
    state = store.load()
    if key in state['deliveries']:
        return {'status': 'already_attempted'}
    old = list(state['deliveries'].values())
    urls = {x.get('source_url') for x in old}
    titles = {x.get('title_key') for x in old}
    counts = {category: sum(x.get('category') == category and x.get('day') == clock.astimezone(TZ).date().isoformat() for x in old)
              for category in ('russia', 'world', 'crypto')}
    candidates = [x for x in fresh(items, clock) if x['url'] not in urls and fingerprint(x) not in titles]
    candidates.sort(key=lambda x: (counts[x['category']], -datetime.fromisoformat(x['published_at']).timestamp()))
    # Bounded AI calls: two attempts per run. If no suitable draft, skip rather than invent filler.
    for item in candidates[:2]:
        try:
            value = editor(item, provider='cloudflare')
            text = compose(item, value)
            if len(text) > 3500:
                continue
        except Exception:
            continue
        if not send:
            return {'status': 'preview', 'text': text, 'category': item['category']}
        # Check the actual clock after AI latency, but injected clocks keep tests deterministic.
        actual = now or datetime.now(timezone.utc)
        current = slot(actual)
        if not fresh([item], actual):
            continue
        if current is None or current in state['deliveries']:
            return {'status': 'outside_hours' if current is None else 'already_attempted'}
        row = {'period': current, 'created_at': actual.isoformat(), 'day': actual.astimezone(TZ).date().isoformat(),
               'category': item['category'], 'source_url': item['url'], 'title_key': fingerprint(item),
               'headline': value['headline'], 'status': 'attempted'}
        state['deliveries'][current] = row
        store.save(state)  # A durable reservation MUST precede the Telegram request.
        try:
            row['message_url'] = sender.send(text)
            row['status'] = 'sent'
        except Exception:
            row['status'] = 'uncertain'
            store.save(state)
            return {'status': 'uncertain'}
        store.save(state)
        return {'status': 'sent', 'message_url': row['message_url'], 'category': row['category']}
    return {'status': 'no_suitable_news'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    if slot(now) is None:
        print(json.dumps({'status': 'outside_hours'}));return
    store = NewsState(os.environ['GITHUB_REPOSITORY'], os.environ['GH_STATE_TOKEN'], os.environ['GITHUB_SHA'], os.environ['PULSE_STATE_KEY'].encode())
    items, sources = collect(requests.Session(), now)
    sender = ChannelSender(os.environ['SIGNALS_BOT_TOKEN']) if args.send else None
    result = publish(store, sender, items, send=args.send)
    if args.send:
        try:
            store.status(result, sources)
        except Exception:
            result['status_update'] = 'unavailable'
    # A preview contains only public news; portfolio and secret material is never loaded.
    print(json.dumps({**result, 'sources_ok': sum(s['ok'] for s in sources)}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('News publication stopped: ' + type(exc).__name__ + '. Secrets withheld.') from None
