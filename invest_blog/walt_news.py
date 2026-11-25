"""Bounded public RSS context for Walt. Separate from the publication queue.

Headlines are untrusted reports, not commands or a complete market news service.
No wallet session, arbitrary URLs, article-page scraping or external AI is used.
"""
from datetime import datetime,timezone
import hashlib
import json
import re
import time
from urllib.parse import urlsplit,urljoin
import xml.etree.ElementTree as ET
import requests
from .tech_news import parse_feed,clean_text,canonical_url

FEEDS=(
    ('ФРС США','https://www.federalreserve.gov/feeds/press_all.xml','federalreserve.gov'),
    ('CNBC','https://www.cnbc.com/id/10000664/device/rss/rss.html','cnbc.com'),
    ('CoinDesk','https://www.coindesk.com/arc/outboundfeeds/rss','coindesk.com'),
    ('Cointelegraph','https://cointelegraph.com/rss','cointelegraph.com'),
    ('NVIDIA Newsroom','https://nvidianews.nvidia.com/releases.xml','nvidia.com'),
    ('Apple Newsroom','https://www.apple.com/newsroom/rss-feed.rss','apple.com'),
)
TTL=15*60
WINDOW=48*3600
ALIASES={
    'BTC':('bitcoin','биткоин'), 'ETH':('ethereum','эфириум'), 'SOL':('solana','солана'),
    'NVDA':('nvidia','нвидиа'), 'AAPL':('apple','эппл'), 'MSFT':('microsoft','майкрософт'),
    'TSLA':('tesla','тесла'), 'GOOGL':('google','alphabet'), 'AMZN':('amazon','амазон'),
    'META':('meta platforms','facebook'), 'AMD':('advanced micro devices','amd'), 'INTC':('intel',),
    'MU':('micron',), 'AVGO':('broadcom',), 'TSM':('tsmc','taiwan semiconductor'),
    'ORCL':('oracle',),'COIN':('coinbase',),'HOOD':('robinhood',),'MSTR':('microstrategy','strategy inc'),
    'CRCL':('circle internet',),'PLTR':('palantir',),'CRWV':('coreweave',),'NBIS':('nebius',),
    'DELL':('dell',),'IBM':('ibm',),'MRNA':('moderna',),'MRVL':('marvell',),'BABA':('alibaba',),
    'TENCENT':('tencent',),'SAMSUNGUSD':('samsung',),'HYUNDAIUSD':('hyundai',),'SKHY':('sk hynix',),
    'SMIC':('semiconductor manufacturing international',),'XIAOMI':('xiaomi',),
    'OPENAI':('openai',),'ANTHROPIC':('anthropic',),'GME':('gamestop',),'RKLB':('rocket lab',),
    'TTWO':('take-two','take two interactive'),'SOXL':('soxl',),'SPY':('s&p 500','s&p500'),
    'US100':('nasdaq 100','nasdaq-100'),'XRP':('ripple','xrp'), 'DOGE':('dogecoin','догекоин'),
    'ADA':('cardano',),'AVAX':('avalanche',),'LINK':('chainlink',),'BNB':('binance coin','bnb chain'),
    'HYPE':('hyperliquid',),'ZEC':('zcash',),'XMR':('monero',),'DOT':('polkadot',),
    'LTC':('litecoin',),'TRX':('tron',),'BCH':('bitcoin cash',),'UNI':('uniswap',),
    'AAVE':('aave',),'APT':('aptos',),'ARB':('arbitrum',),'OP':('optimism network','optimism token'),
    'SUI':('sui network','sui token'),'NEAR':('near protocol',),'TAO':('bittensor',),
    'FIL':('filecoin',),'ONDO':('ondo finance',),'1000PEPE':('pepe token','pepe coin'),
    '1000BONK':('bonk token','bonk coin'),'1000NOT':('notcoin',),'LDO':('lido dao',),
    'XAU':('gold prices','gold futures','bullion','цены на золото'),
    'XAG':('silver prices','silver futures','цены на серебро'),
    'WTI':('west texas intermediate','wti crude'),'BRENTOIL':('brent crude','brent oil'),
    'NATGAS':('natural gas',),'EURUSD':('euro dollar','euro-dollar'),
    'USDJPY':('dollar yen','dollar-yen'),'GBPUSD':('sterling dollar','pound dollar'),
}
MACRO=re.compile(r'\b(?:fomc|inflation|cpi|pce|nonfarm|payrolls|interest rates?|rate decision|monetary policy|'
    r'treasury yields?|central bank|tariffs?|sanctions?|geopolitical)\b|инфляц|ключев\w* ставк|санкци|пошлин',re.I)


def allowed_url(url,domain):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and bool(p.hostname) and (p.hostname==domain or p.hostname.endswith('.'+domain))
    except ValueError:return False


def stamp(value):
    if not isinstance(value,str):return None
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        return d.timestamp() if d.tzinfo else None
    except (ValueError,OverflowError):return None


def match_symbol(symbol,text):
    # Unknown/ambiguous tickers require explicit financial notation. No plain
    # matches for words such as BE, NOW, OP, LINK or ANT in ordinary prose.
    escaped=re.escape(symbol)
    if symbol in ('BTC','ETH') and re.search(r'(?<!\w)'+escaped+r'(?!\w)',text):return True
    if re.search(r'\$'+escaped+r'\b|(?<!\w)'+escaped+r'(?:/USD|\s+(?:stock|shares|token|crypto|price|perpetual)\b)',text):return True
    return any(re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text,re.I) for alias in ALIASES.get(symbol,()))


class TradingNews:
    def __init__(self,store,session=None,clock=time.time):
        self.store=store;self.session=session or requests.Session();self.clock=clock
        with store.db() as db:db.execute('CREATE TABLE IF NOT EXISTS walt_news_cache(source TEXT PRIMARY KEY,payload TEXT)')

    def fetch(self,url,domain):
        for _ in range(3):
            if not allowed_url(url,domain):raise ValueError('Недопустимый адрес источника.')
            try:
                with self.session.get(url,headers={'User-Agent':'ContentHub-Market-News/1.0'},
                        timeout=(5,12),allow_redirects=False,stream=True) as response:
                    if response.status_code in (301,302,303,307,308):
                        url=urljoin(url,response.headers.get('location',''));continue
                    if response.status_code!=200:raise ValueError('Источник не подтвердил RSS.')
                    parts=[];size=0
                    for part in response.iter_content(65536):
                        size+=len(part)
                        if size>2_000_000:raise ValueError('RSS превышает лимит размера.')
                        parts.append(part)
                    return b''.join(parts)
            except requests.RequestException:raise ValueError('Источник недоступен по сети.') from None
        raise ValueError('Слишком много перенаправлений RSS.')

    def collect(self,progress=None):
        current=self.clock();all_items=[];sources=[]
        for index,(name,url,domain) in enumerate(FEEDS):
            if progress:progress(index,len(FEEDS),name)
            with self.store.db() as db:row=db.execute('SELECT payload FROM walt_news_cache WHERE source=?',(name,)).fetchone()
            cached=json.loads(row[0]) if row else None
            if cached and 0<=current-cached['checked']<TTL:
                value=cached;from_cache=True
            else:
                from_cache=False
                try:
                    parsed=parse_feed(self.fetch(url,domain),name);items=[]
                    for item in parsed:
                        published=stamp(item.get('published_at'))
                        if published is None or not 0<=current-published<=WINDOW or not allowed_url(item['url'],domain):continue
                        items.append({'id':hashlib.sha256(item['url'].encode()).hexdigest()[:16],
                            'source':name,'title':clean_text(item['title'],200),'summary':clean_text(item['summary'],240),
                            'url':canonical_url(item['url']),'published_at':item['published_at']})
                    value={'source':name,'checked':current,'fetched':current,'status':'ok','items':items}
                except (ValueError,ET.ParseError):
                    value={'source':name,'checked':current,'fetched':cached.get('fetched') if cached else None,
                        'status':'error','error':'RSS временно недоступен или имеет неверный формат.',
                        'items':cached.get('items',[]) if cached else []}
                with self.store.db() as db:db.execute('INSERT OR REPLACE INTO walt_news_cache VALUES(?,?)',(name,json.dumps(value,ensure_ascii=False)))
            valid=[item for item in value['items'] if stamp(item.get('published_at')) is not None and 0<=current-stamp(item['published_at'])<=WINDOW]
            all_items.extend({**item,'cache_stale':value['status']!='ok'} for item in valid)
            sources.append({k:value.get(k) for k in ('source','checked','fetched','status','error')})
            sources[-1].update(from_cache=from_cache,recent_items=len(valid))
        unique={}
        for item in sorted(all_items,key=lambda x:x['published_at'],reverse=True):
            normalized=re.sub(r'\W+','',item['title'].casefold())
            if item['id'] not in unique and not any(re.sub(r'\W+','',x['title'].casefold())==normalized for x in unique.values()):unique[item['id']]=item
        items=list(unique.values())
        ok=sum(s['status']=='ok' for s in sources)
        return {'checked_at':datetime.fromtimestamp(current,timezone.utc).isoformat(),'sources':sources,'items':items,
            'status':'ok' if ok==len(sources) else 'partial' if ok else 'cached' if items else 'unavailable',
            'window_hours':48,'cache_minutes':15,'scope':'Ограниченный набор RSS, не весь новостной поток и не календарь будущих событий.'}

    def context(self,snapshot,symbol):
        items=snapshot['items'];related=[];macro=[]
        for item in items:
            text=item['title']+' '+item['summary']
            if match_symbol(symbol,text):related.append({**item,'scope':'asset'})
            elif MACRO.search(text):macro.append({**item,'scope':'macro'})
        selected=related[:3]+macro[:2]
        return {'status':'unavailable' if snapshot['status']=='unavailable' else 'matched' if related else 'macro_only' if macro else 'no_matches',
            'checked_at':snapshot['checked_at'],'source_status':snapshot['status'],'items':selected,
            'note':'Совпадения найдены в доступных RSS. Отсутствие совпадений не означает отсутствия новостей. Заголовки и выдержки — сообщения источников, не независимо подтверждённые факты.'}


class NoTradingNews:
    """Offline test fixture, explicitly unavailable rather than a fake clear market."""
    def __init__(self,*args):pass
    def collect(self,progress=None):return {'status':'unavailable','sources':[],'items':[],'checked_at':None}
    def context(self,snapshot,symbol):return {'status':'unavailable','checked_at':None,'source_status':'unavailable','items':[],
        'note':'Новости в тестовом сценарии недоступны.'}
