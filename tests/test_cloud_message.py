"""Readable Telegram reports preserve scenario meaning and market provenance."""
from copy import deepcopy
from invest_blog.cloud_signals import report,display_rows,deliver,review
from test_cloud_signals import packet,Session,Ledger
from test_cloud_scenarios import bars
from invest_blog.cloud_scenarios import describe


def market(symbol,ident,change,volume=2_000_000):
    r=deepcopy(packet()['rows'][0]);r.update(symbol=symbol,market_id=ident,side='wait',volume_24h_quote=volume,scenario=describe(bars(-1)))
    r['metrics']['change_24h_pct']=change
    return r


def test_no_entries_still_shows_liquid_movers_but_not_thin_pumps():
    p=packet();p['rows']=[market('BTC',0,-1),market('ETH',1,-.5),market('LINK',2,-5),market('GRAM',3,-3),market('THIN',4,90,10)]
    assert [r['symbol'] for r in display_rows(p['rows'])]==['LINK','GRAM','BTC']
    value=report(p,rich=True)
    assert '⚡️ <b>Рыночные сигналы</b>' in value and '19:17 МСК' in value
    assert 'Индикаторы: снижение' in value and '<b>LINK</b>' in value
    assert 'Подтверждённых входов сейчас нет' in value and 'THIN' not in value
    assert 'не спот-пары /USDT' in value and 'BTC/USDT' not in value
    assert len(value)<3000


def test_telegram_uses_html_and_escapes_untrusted_news():
    p=packet();p['news']['items']=[{'source':'ФРС','title':'<b>untrusted & news</b>'}]
    s=Session();deliver(p,'SECRET','12345',Ledger(),s)
    message=next(kw['json'] for url,kw in s.calls if url.endswith('sendMessage'))
    assert message['parse_mode']=='HTML'
    assert '&lt;b&gt;untrusted &amp; news&lt;/b&gt;' in message['text']
    assert '<b>untrusted' not in message['text']


def test_model_reviews_the_displayed_movers_even_without_entry_candidates():
    p=packet();p['rows']=[market('BTC',0,-1),market('ETH',1,-.5),market('LINK',2,-5),market('GRAM',3,-3),market('SOL',4,-.2),market('NVDA',5,.1)]
    s=Session({'reviews':[{'market_id':r['market_id'],'accept':False,'reason':'trend_unclear'} for r in p['rows']]})
    review(p,'a'*32,'SECRET',s)
    assert p['ai']['status']=='ok'
    assert all('ai_review' in r for r in display_rows(p['rows']))
