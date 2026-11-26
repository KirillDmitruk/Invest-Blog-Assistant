"""Scheduled public Telegram news. No portfolio, trading or private wallet access."""
import argparse
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .cloud_pulse import PrivateState
from .cloud_signals import json_call, ServiceCallError
from .news_editor import generate, compose
from .tech_news import FEEDS, OFFICIAL_SOURCES, RSS_HEADERS, parse_feed

LEGACY_CHANNEL = '@kirintechnews'

def target_channel():
    value=os.getenv('NEWS_TELEGRAM_CHANNEL','').strip()
    if not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}',value):
        raise ValueError('Инвестиционный канал не настроен. Отправка заблокирована.')
    return value.lower()
TZ = ZoneInfo('Europe/Moscow')


class NewsState(PrivateState):
    branch = 'automation/telegram-news-state'
    filename = 'news-state.enc'

    def status(self, result, sources):
        state = self.load()
        channel=target_channel()
        rows = [r for r in state['deliveries'].values() if r.get('channel',LEGACY_CHANNEL)==channel]
        sent = sorted((r for r in rows if r['status'] == 'sent'), key=lambda r: r['created_at'])
        now = datetime.now(timezone.utc)
        payload = {'checked_at': now.isoformat(), 'result': result['status'], 'channel': channel,
                   'today_sent': sum(r['day'] == now.astimezone(TZ).date().isoformat() for r in sent),
                   'last_post': ({k: sent[-1].get(k) for k in ('headline', 'message_url', 'created_at','media_mode')} if sent else None),
                   'sources': sources, 'pending_count':len(state.get('pending_news',[]))}
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
    return str(int(clock.timestamp()) // 600) if 9 <= local.hour < 23 else None


def fresh(items, clock):
    out = []
    for item in items:
        try:
            stamp = datetime.fromisoformat(item['published_at'])
            if stamp.tzinfo is None or not timedelta(0) <= clock - stamp <= timedelta(hours=24):
                continue
            if item['category'] not in ('russia', 'world', 'crypto') or len(item['summary']) < 60:
                continue
            text = item['title'] + ' ' + item['summary']
            if re.search(r'sponsored|промокод|розыгрыш|партн[её]рский материал', text, re.I) or (item['source'] not in OFFICIAL_SOURCES and re.search(r'press release',text,re.I)):
                continue
            if not re.search(r'рын[окк]|бирж|акци[яийю]|облигац|дивиденд|купон|выруч|прибыл|убыт|отч[её]т|инфляц|ключев.{0,12}ставк|рубл|нефт|цен[аыу]|экономик|фрс|битко|крипт|эфириум|bitcoin|ethereum|crypto|stock|shares|earnings|revenue|inflation|interest rate|bond|yield|nasdaq|s&p|etf|oil price|финанс|ликвидност|ценн.{0,12}бумаг|monetary|securities|federal reserve|central bank|liquidity|financial|stablecoin|tokenized', text, re.I):
                continue
            out.append(item)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fingerprint(item):
    # A normalized headline protects against identical stories syndicated at other URLs.
    return hashlib.sha256(re.sub(r'\W+', ' ', item['title'].casefold()).strip().encode()).hexdigest()


def collect(session, clock):
    items, sources = [], []
    for name, url in FEEDS:
        try:
            with session.get(url, timeout=(5, 15), headers=RSS_HEADERS, stream=True) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 2_000_000:
                        raise ValueError('RSS too large')
                    chunks.append(chunk)
            found = parse_feed(b''.join(chunks), name)
            if not found:raise ValueError('No readable feed entries')
            eligible = fresh(found, clock)
            items.extend(found)
            sources.append({'source': name, 'ok': True, 'found':len(found), 'eligible':len(eligible),
                            'latest_at':max((x['published_at'] for x in found if x['published_at']), default=None)})
        except Exception:
            sources.append({'source': name, 'ok': False})
    return fresh(items, clock), sources


class ChannelSender:
    def __init__(self, token, session=None, channel=None):
        from html import escape
        self.escape = escape
        self.channel=channel or target_channel()
        if not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}',self.channel):raise ValueError('Неверный канал')
        self.channel=self.channel.lower()
        self.session = session or requests.Session()
        self.url = 'https://api.telegram.org/bot' + token + '/'
        me = json_call(self.session, 'POST', self.url + 'getMe', json={})
        chat = json_call(self.session, 'POST', self.url + 'getChat', json={'chat_id': self.channel})
        result = chat.get('result', {})
        if not me.get('ok') or not chat.get('ok') or result.get('type') != 'channel' or result.get('username', '').lower() != self.channel[1:]:
            raise ValueError('Канал не подтверждён')
        member = json_call(self.session, 'POST', self.url + 'getChatMember', json={'chat_id': result['id'], 'user_id': me['result']['id']})
        rights = member.get('result', {})
        if not member.get('ok') or not (rights.get('status') == 'creator' or rights.get('status') == 'administrator' and rights.get('can_post_messages')):
            raise ValueError('Право публикации не подтверждено')
        self.chat = result['id']

    def send(self, text, *, photo=None, source_url=None):
        from .news_article import source_image,download_photo
        photo=source_image(photo)
        lines=text.split('\n')
        html='\n'.join('<b>'+self.escape(line)+'</b>' if i==0 else self.escape(line) for i,line in enumerate(lines))
        result=None;self.media_mode='text'
        image=None
        if photo and len(text.encode('utf-16-le'))//2<=1024:
            try:image=download_photo(photo)
            except Exception:pass
        if image is not None:
            try:
                result=json_call(self.session,'POST',self.url+'sendPhoto',data={
                    'chat_id':self.chat,'caption':html,'parse_mode':'HTML'},files={'photo':(image[1],image[0],image[2])})
                if result.get('ok') is False and result.get('error_code')==400:result=None
                else:self.media_mode='photo'
            except ServiceCallError as exc:
                if exc.http_status!=400:raise
                # Explicit rejection means no photo was sent. An ambiguous error never retries.
                result=None
        if result is None:
            preview={'is_disabled':True}
            if photo and source_url:
                from urllib.parse import urlsplit
                p=urlsplit(source_url)
                if p.scheme=='https' and p.hostname=='smart-lab.ru' and not p.username and not p.password:
                    preview={'is_disabled':False,'url':source_url,'prefer_large_media':True,'show_above_text':True}
                    self.media_mode='link_preview'
            result=json_call(self.session,'POST',self.url+'sendMessage',json={
                'chat_id':self.chat,'text':html,'parse_mode':'HTML','link_preview_options':preview})
        if not result.get('ok') or type(result.get('result',{}).get('message_id')) is not int:
            raise ValueError('Отправка не подтверждена')
        return 'https://t.me/'+self.channel[1:]+'/'+str(result['result']['message_id'])


def publish(store, sender, items, *, editor=generate, now=None, send=False, enricher=None, target=None):
    clock = now or datetime.now(timezone.utc)
    raw_slot=slot(clock)
    channel=getattr(sender,'channel',None) or target
    prefix=hashlib.sha256(channel.encode()).hexdigest()[:12]+':' if channel else ''
    key=prefix+raw_slot if raw_slot is not None else None
    if key is None:
        return {'status': 'outside_hours'}
    state = store.load()
    old = list(state['deliveries'].values())
    urls = {x.get('source_url') for x in old}
    titles = {x.get('title_key') for x in old}
    counts = {category: sum((channel is None or x.get('channel',LEGACY_CHANNEL)==channel) and x.get('category') == category and x.get('day') == clock.astimezone(TZ).date().isoformat() for x in old)
              for category in ('russia', 'world', 'crypto')}
    # Save observed stories before publishing; RSS windows can roll over between slots.
    pending={}
    for item in fresh([*state.get('pending_news',[]),*items],clock):
        if item['url'] not in urls and fingerprint(item) not in titles:
            pending[item['url']]=item
    queue=[];queue_bytes=0
    for item in sorted(pending.values(),key=lambda x:x['published_at'],reverse=True)[:500]:
        item={k:item[k] for k in ('source','title','url','summary','category','published_at')}
        size=len(json.dumps(item,ensure_ascii=False).encode())
        if queue_bytes+size>400_000:break
        queue.append(item);queue_bytes+=size
    if send and queue!=state.get('pending_news',[]):
        state['pending_news']=queue
        store.save(state)
    # Enforce elapsed time too: slot boundaries and legacy half-hour keys must not
    # allow two posts a minute apart. Uncertain attempts also consume the interval.
    recent=False
    for row in old:
        try:
            if (channel is None or row.get('channel',LEGACY_CHANNEL)==channel) and timedelta(0)<=clock-datetime.fromisoformat(row['created_at'])<timedelta(minutes=10):recent=True
        except (KeyError,ValueError,TypeError):continue
    if key in state['deliveries'] or recent:
        return {'status':'already_attempted'}
    candidates = queue
    candidates.sort(key=lambda x:-datetime.fromisoformat(x['published_at']).timestamp())
    pools={c:[x for x in candidates if x['category']==c] for c in sorted(counts,key=counts.get)}
    candidates=[pool[i] for i in range(3) for pool in pools.values() if len(pool)>i]
    # Bounded AI calls: two attempts per run. If no suitable draft, skip rather than invent filler.
    rejected={};ai_attempts=0
    for item in candidates:
        if ai_attempts>=2:break
        try:
            if enricher is not None:
                item = enricher(item)
            from difflib import SequenceMatcher
            if SequenceMatcher(None,item['title'].casefold(),item['summary'].casefold()).ratio()>.9:
                continue
            ai_attempts+=1
            value = editor(item, provider='cloudflare')
            text = compose(item, value)
            if len(text) > 3500:
                continue
        except Exception as exc:
            code=getattr(exc,'code','article_or_format')
            if code not in ('schema','headline','paragraphs','length','content','language','numbers','unavailable'):code='article_or_format'
            rejected[code]=rejected.get(code,0)+1
            continue
        if not send:
            return {'status': 'preview', 'text': text, 'category': item['category'], 'image_url':item.get('image_url')}
        # Check the actual clock after AI latency, but injected clocks keep tests deterministic.
        actual = now or datetime.now(timezone.utc)
        actual_slot=slot(actual)
        current=prefix+actual_slot if actual_slot is not None else None
        if not fresh([item], actual):
            continue
        if current is None or current in state['deliveries']:
            return {'status': 'outside_hours' if current is None else 'already_attempted'}
        row = {'period': current, 'created_at': actual.isoformat(), 'day': actual.astimezone(TZ).date().isoformat(),
               'category': item['category'], 'source_url': item['url'], 'title_key': fingerprint(item),
               'headline': value['headline'], 'status': 'attempted', 'channel':channel}
        state['deliveries'][current] = row
        store.save(state)  # A durable reservation MUST precede the Telegram request.
        try:
            if item.get('image_url'):
                row['message_url']=sender.send(text,photo=item['image_url'],source_url=item['url'])
            else:row['message_url']=sender.send(text)
            row['media_mode']=getattr(sender,'media_mode','text')
            row['status'] = 'sent'
        except Exception:
            row['status'] = 'uncertain'
            store.save(state)
            return {'status': 'uncertain'}
        store.save(state)
        return {'status': 'sent', 'message_url': row['message_url'], 'category': row['category'], 'media_mode':row['media_mode']}
    return {'status': 'no_suitable_news', 'rejected':rejected}


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
    from .news_article import enrich
    article_session = requests.Session()
    result = publish(store, sender, items, send=args.send, enricher=lambda item: enrich(item,article_session),target=target_channel())
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
