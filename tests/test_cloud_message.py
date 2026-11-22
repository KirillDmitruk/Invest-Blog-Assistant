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


def test_model_prompt_is_compact_and_old_approval_is_cleared():
    import json
    p=packet();p['rows']=[market('ASSET'+str(i),i,-i) for i in range(8)]
    for row in p['rows']:
        row['scenario']['advice']='x'*20000
        row['ai_review']={'accept':True,'reason':'checks_pass'}
    s=Session({'reviews':[{'market_id':i,'accept':False,'reason':'trend_unclear'} for i in range(8)]})
    review(p,'a'*32,'SECRET',s)
    body=s.calls[0][1]['json']['messages'][1]['content']
    assert len(body)<12000 and 'advice' not in body
    assert p['ai']['status']=='ok'
    review(p,'','',s)
    assert p['ai']['status']=='not_configured' and all('ai_review' not in row for row in p['rows'])


def test_cloudflare_copy_whitespace_and_bearer_prefix_are_normalized():
    p=packet();s=Session({'reviews':[{'market_id':0,'accept':True,'reason':'checks_pass'}]})
    review(p,' '+('a'*32)+'\n','\r\nBearer SECRET\r\n',s)
    assert p['ai']['status']=='ok'
    assert s.calls[0][1]['headers']['Authorization']=='Bearer SECRET'
    s=Session();review(p,'a'*32,'SECRET\nINJECTED',s)
    assert p['ai']['diagnostic']['kind']=='credential_format' and not s.calls


def test_current_cloudflare_model_accepts_chat_completion_response(monkeypatch):
    import invest_blog.cloud_signals as module
    import json
    assert module.MODEL=='@cf/google/gemma-4-26b-a4b-it'
    answer=json.dumps({'reviews':[{'market_id':0,'accept':True,'reason':'checks_pass'}]})
    for data in ({'success':True,'result':{'choices':[{'message':{'content':answer}}]}},
                 {'choices':[{'message':{'content':answer}}]}):
        monkeypatch.setattr(module,'json_call',lambda *a,**kw:data)
        p=packet();review(p,'a'*32,'SECRET')
        assert p['ai']['status']=='ok'
