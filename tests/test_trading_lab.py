"""PAPER-only futures maths and independent risk checks; no real orders or AI calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Event
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import pytest
import requests

from invest_blog.trading_lab import TradingLab, account_values
from invest_blog.trading_market import FuturesMarket, indicators
from invest_blog.trading_ai import propose, check_xai_access


CLOCK = datetime(2026, 11, 14, 10, tzinfo=timezone.utc)


def contract(uid='a', margin='500', tick_value='1'):
    return {'uid':uid,'ticker':'TEST'+uid,'name':'Test future','tick':'1','tick_value':tick_value,
            'margin_long':margin,'margin_short':margin,'expires_at':(CLOCK+timedelta(days=100)).isoformat(),
            'source':'test','as_of':CLOCK.isoformat()}


def bars(down=False, clock=CLOCK):
    result=[]
    for i in range(35):
        close=Decimal(200-i if down else 100+i)
        result.append({'time':(clock-timedelta(minutes=(35-i)*15)).isoformat(),'open':str(close),
                       'close':str(close),'high':str(close+Decimal('.1')),'low':str(close-Decimal('.1'))})
    return result


class Market:
    def __init__(self, down=False, margin='500', tick_value='1'):
        self.price='165' if down else '135';self.down=down;self.margin=margin;self.tick_value=tick_value
        self.tradeable=True;self.clock=CLOCK;self.bar_clock=CLOCK
    def snapshot(self, uid, clock=None):
        return {'contract':contract(uid,self.margin,self.tick_value),'price':self.price,
                'quoted_at':self.clock.isoformat(),'tradeable':self.tradeable,'candles':bars(self.down,self.bar_clock)}


@pytest.fixture
def lab(tmp_path,monkeypatch):
    monkeypatch.delenv('TRADING_AI_API_KEY',raising=False)
    monkeypatch.delenv('XAI_API_KEY',raising=False)
    monkeypatch.setenv('TRADING_AI_PROVIDER','none')
    result=TradingLab(tmp_path/'test.db')
    result.watch(contract())
    enable(result)
    return result


def enable(lab, strategy='rules', **risk_changes):
    state=lab.overview()['state']
    risk=deepcopy(state['risk']);risk.update(risk_changes)
    return lab.configure({'budget':state['budget'],'enabled':True,'strategy':strategy,'risk':risk},state['config_revision'])


def test_initial_account_is_paper_and_has_no_fabricated_positions(tmp_path):
    result=TradingLab(tmp_path/'test.db').overview()
    assert result['state']['enabled'] is False
    assert result['state']['strategy']=='ai'
    assert result['account']['equity']=='10000.00'
    assert not result['state']['positions'] and not result['history']
    assert result['live_orders_available'] is False


def test_long_fill_counts_entry_slippage_and_commission_immediately(lab):
    market=Market();lab.cycle(market,CLOCK)
    result=lab.overview();p=result['state']['positions']['a']
    assert p['side']=='long' and p['contracts']==1
    assert p['entry']=='136' and p['last_price']=='135'
    assert p['accrued_pnl']=='-1'
    assert result['account']['cash']=='9998.00'
    assert result['account']['equity']=='9997.00'
    assert result['account']['margin']=='625.00'
    assert len(result['trades'])==1


@pytest.mark.parametrize('down',[False,True])
def test_long_and_short_gap_stop_use_observed_price_not_ideal_stop(lab,down):
    market=Market(down=down);lab.cycle(market,CLOCK)
    market.price='170' if down else '130';market.clock=CLOCK+timedelta(minutes=5)
    lab.cycle(market,market.clock)
    result=lab.overview()
    assert result['state']['positions']=={}
    assert result['account']['equity']=='9989.00'
    assert result['trades'][0]['detail']['price']==('171' if down else '129')
    assert result['trades'][0]['detail']['fee']=='2.00'
    assert result['trades'][0]['detail']['reason']=='Стоп'


def test_margin_and_risk_can_reject_even_one_contract(lab):
    lab.cycle(Market(margin='18000'),CLOCK)
    assert lab.overview()['trades']==[]
    assert 'не проходит' in lab.overview()['decisions'][0]['detail']['result']
    assert lab.overview()['account']['equity']=='10000.00'


def test_position_limit_and_one_decision_per_closed_candle(lab):
    lab.watch(contract('b'));enable(lab,max_positions=1)
    market=Market();lab.cycle(market,CLOCK);lab.cycle(market,CLOCK+timedelta(minutes=1))
    result=lab.overview()
    assert len(result['state']['positions'])==1 and len(result['trades'])==1
    assert len(result['decisions'])==2
    assert any('лимит открытых' in r['detail']['result'] for r in result['decisions'])


def test_daily_loss_pauses_and_closes_fresh_positions(lab):
    market=Market(tick_value='10');lab.cycle(market,CLOCK)
    assert lab.overview()['state']['positions']
    market.price='50';market.clock=CLOCK+timedelta(minutes=5)
    lab.cycle(market,market.clock)
    result=lab.overview()
    assert result['state']['enabled'] is False
    assert result['state']['auto_pause_reason']
    assert result['state']['positions']=={}
    assert Decimal(result['account']['equity'])<Decimal('9700')


def test_new_day_does_not_hide_overnight_gap(lab):
    market=Market(tick_value='10');lab.cycle(market,CLOCK)
    prior=lab.overview()['account']['equity']
    market.price='50';market.clock=CLOCK+timedelta(days=1)
    lab.cycle(market,market.clock)
    assert lab.overview()['state']['day_start_equity']==prior
    assert lab.overview()['state']['enabled'] is False


def test_pause_during_model_request_prevents_fill(lab,monkeypatch):
    monkeypatch.setenv('TRADING_AI_PROVIDER','xai');monkeypatch.setenv('TRADING_AI_MODEL','test');monkeypatch.setenv('XAI_API_KEY','not-real')
    enable(lab,strategy='ai');entered=Event();released=Event()
    def slow_ai(*args):
        entered.set();assert released.wait(5)
        return {'action':'long','confidence':.9,'reason':'Test'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(lab.cycle,Market(),CLOCK,slow_ai)
        assert entered.wait(5);lab.pause();released.set();future.result(timeout=5)
    assert lab.overview()['trades']==[]
    assert 'пауза' in lab.overview()['decisions'][0]['detail']['result']


def test_quote_failure_does_not_zero_an_existing_position(lab):
    market=Market();lab.cycle(market,CLOCK)
    before=lab.overview()['account']['equity']
    class Missing:
        def snapshot(self,*args):raise ValueError('Нет свежей цены')
    lab.cycle(Missing(),CLOCK+timedelta(minutes=5))
    assert lab.overview()['account']['equity']==before
    assert lab.overview()['state']['positions']['a']['quoted_at']==CLOCK.isoformat()


def test_margin_increase_triggers_pause(lab):
    market=Market();lab.cycle(market,CLOCK)
    market.margin='20000';market.clock=CLOCK+timedelta(minutes=5)
    lab.cycle(market,market.clock)
    assert lab.overview()['state']['enabled'] is False
    assert not lab.overview()['state']['positions']


def test_tick_value_changes_only_revalue_new_observed_movements(lab):
    market=Market();lab.cycle(market,CLOCK)
    market.price='136';market.tick_value='2';market.clock=CLOCK+timedelta(minutes=5)
    lab.cycle(market,market.clock)
    p=lab.overview()['state']['positions']['a']
    assert Decimal(p['accrued_pnl'])==1
    assert p['tick_value']=='2'


def test_budget_cannot_rewrite_existing_history(lab):
    lab.cycle(Market(),CLOCK)
    s=lab.overview()['state']
    with pytest.raises(ValueError,match='Бюджет'):
        lab.configure({'budget':'20000','enabled':False,'strategy':'rules','risk':s['risk']},s['config_revision'])
    assert lab.overview()['state']['budget']=='10000'


def test_cycle_lease_prevents_parallel_work(lab):
    owner=lab._acquire(CLOCK)
    assert owner
    assert lab.cycle(Market(),CLOCK)['status']=='busy'
    assert not lab.overview()['trades']


def test_read_only_gateway_rejects_all_order_and_account_methods():
    class API:
        def call(self,*args):pytest.fail('Forbidden endpoint reached transport')
    market=FuturesMarket('',None,API())
    for service,method in [('OrdersService','PostOrder'),('SandboxService','PostSandboxOrder'),('UsersService','GetAccounts')]:
        with pytest.raises(ValueError,match='только чтение'):market.read(service,method,{})


def quote(v):return {'units':str(v),'nano':0}


class RawAPI:
    def __init__(self,stale=False,missing_tick=False):self.stale=stale;self.missing_tick=missing_tick
    def call(self,service,method,payload):
        if method=='FutureBy':return {'instrument':{'ticker':'TEST','lot':1,'expirationDate':(CLOCK+timedelta(days=100)).isoformat()}}
        if method=='GetFuturesMargin':return {'minPriceIncrement':quote(1),'minPriceIncrementAmount':quote(0 if self.missing_tick else 1),'initialMarginOnBuy':{**quote(500),'currency':'rub'},'initialMarginOnSell':{**quote(600),'currency':'rub'}}
        if method=='GetLastPrices':return {'lastPrices':[{'instrumentUid':'a','price':quote(135),'time':(CLOCK-timedelta(hours=2) if self.stale else CLOCK).isoformat()}]}
        if method=='GetTradingStatus':return {'tradingStatus':'SECURITY_TRADING_STATUS_NORMAL_TRADING','apiTradeAvailableFlag':True}
        if method=='GetCandles':
            data=[{**{k:quote(100+i) for k in ('open','high','low','close')},'time':(CLOCK-timedelta(minutes=(35-i)*15)).isoformat(),'isComplete':True} for i in range(35)]
            data.append({**{k:quote(999999) for k in ('open','high','low','close')},'time':CLOCK.isoformat(),'isComplete':False})
            return {'candles':data}
        raise AssertionError(method)


def test_only_completed_nonfuture_candles_are_used():
    snapshot=FuturesMarket('',None,RawAPI()).snapshot('a',CLOCK)
    assert len(snapshot['candles'])==35
    assert indicators(snapshot['candles'])['last_close']=='134.000000000'
    with pytest.raises(ValueError,match='устарела'):FuturesMarket('',None,RawAPI(stale=True)).snapshot('a',CLOCK)
    with pytest.raises(ValueError):FuturesMarket('',None,RawAPI(missing_tick=True)).snapshot('a',CLOCK)


class AIResponse:
    def __init__(self,text):self.text=text
    def raise_for_status(self):pass
    def json(self):return {'output':[{'content':[{'type':'output_text','text':self.text}]}]}


class AISession:
    def __init__(self,text):self.text=text;self.calls=[]
    def post(self,url,**kwargs):self.calls.append((url,kwargs));return AIResponse(self.text)


@pytest.fixture
def xai(monkeypatch):
    monkeypatch.setenv('TRADING_AI_PROVIDER','xai');monkeypatch.setenv('TRADING_AI_MODEL','test-model');monkeypatch.setenv('XAI_API_KEY','SECRET')
    monkeypatch.delenv('TRADING_AI_API_KEY',raising=False)


def test_xai_adapter_returns_only_validated_proposal(xai):
    session=AISession(json.dumps({'action':'hold','confidence':.5,'reason':'Недостаточно данных.'}))
    assert propose({'sma_fast':'10'},session=session)['action']=='hold'
    url,kwargs=session.calls[0]
    assert url=='https://api.x.ai/v1/responses'
    assert kwargs['allow_redirects'] is False
    assert kwargs['json']['max_output_tokens']==1000
    assert 'SECRET' not in json.dumps(kwargs['json'])


@pytest.mark.parametrize('text',['not json','{"action":"live_order","confidence":1,"reason":"x"}',
    '{"action":"long","confidence":NaN,"reason":"x"}',
    '{"action":"long","confidence":1,"reason":"x","quantity":1000}'])
def test_malformed_model_responses_do_not_bypass_risk(xai,text):
    session=AISession(text)
    with pytest.raises(ValueError):propose({},session=session)
    assert len(session.calls)==1


def test_model_transport_errors_never_expose_key_or_retry(xai):
    class Broken:
        def post(self,*a,**k):raise requests.ConnectionError('SECRET')
    with pytest.raises(ValueError) as error:propose({},session=Broken())
    assert 'SECRET' not in str(error.value)


def test_all_api_writes_require_csrf_and_live_mode_is_rejected(tmp_path):
    from invest_blog.dashboard import create_app
    with TestClient(create_app(tmp_path/'app.db')) as client:
        s=client.get('/api/trading/overview').json()['state']
        body={'revision':s['config_revision'],'budget':'10000','enabled':False,'strategy':'rules','risk':s['risk']}
        assert client.put('/api/trading/settings',json=body).status_code==403
        assert client.post('/api/trading/cycle').status_code==403
        assert client.post('/api/trading/ai-check').status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        assert client.put('/api/trading/settings',headers=headers,json={**body,'mode':'live'}).status_code==422
        assert client.put('/api/trading/settings',headers=headers,json={**body,'budget':'NaN'}).status_code==422
        assert client.put('/api/trading/settings',headers=headers,json={**body,'risk':{**s['risk'],'risk_pct':10}}).status_code==422
        assert client.put('/api/trading/settings',headers=headers,json=body).status_code==200
        assert client.post('/api/trading/orders',headers=headers,json={}).status_code==404
        assert client.get('/static/trading.js').status_code==200
        assert client.get('/static/hub-theme.css').status_code==200

def test_closed_trade_keeps_entry_margin_when_current_margin_changes(lab):
    market=Market();lab.cycle(market,CLOCK)
    entry=lab.overview()['state']['positions']['a']
    assert Decimal(entry['entry_margin'])==Decimal('625')
    market.margin='600';market.clock=CLOCK+timedelta(minutes=5);lab.cycle(market,market.clock)
    assert Decimal(lab.overview()['state']['positions']['a']['reserved_margin'])==Decimal('750')
    lab.close_position('a',market,market.clock)
    detail=lab.overview()['trades'][0]['detail']
    assert Decimal(detail['entry_margin'])==Decimal('625')
    assert detail['entry_fee']=='2.00'

def test_legacy_trade_never_uses_current_margin_as_entry_margin(lab):
    market=Market();lab.cycle(market,CLOCK)
    with lab.db() as db:
        state=lab._load(db);state['positions']['a'].pop('entry_margin');state['positions']['a'].pop('entry_fee');lab._save(db,state)
    lab.close_position('a',market,CLOCK+timedelta(minutes=1))
    detail=lab.overview()['trades'][0]['detail']
    assert detail['entry_margin'] is None and detail['entry_fee'] is None
