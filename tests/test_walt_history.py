"""History integrity, no-lookahead simulation and bounded local model authority."""
from copy import deepcopy
import json,math,time
import pytest
from invest_blog.walt_history import (LighterHistory,parse_candles,indicator_series,technical_side,
    backtest,review_candidates,assess,STRATEGY,HOUR)
from invest_blog.walt_news import NoTradingNews
from invest_blog.walt_trading import WaltTrading,safe_signal
from test_walt_trading import FakeAgent,contract,DEFAULT_LIMITS,DemoHistory

CUTOFF=1_795_000_000//HOUR*HOUR


def bars(count=480):
    return [{'t':CUTOFF-(count-i)*HOUR,'o':100.,'h':101.,'l':99.,'c':100.,'v':10.,'V':1000.} for i in range(count)]


def raw(rows):return {'code':200,'r':'1h','c':[{**b,'t':b['t']*1000} for b in rows]}


class Response:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,*args):yield json.dumps(self.data).encode()


class Session:
    def __init__(self):self.calls=[];self.status=200;self.data=raw(bars());self.markets=[{
        'market_id':0,'market_type':'perp','symbol':'BTC','status':'active','is_frozen':False,'multiplier':'1'}]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        assert url in ('https://mainnet.zklighter.elliot.ai/api/v1/orderBooks','https://mainnet.zklighter.elliot.ai/api/v1/candles')
        assert kwargs['allow_redirects'] is False and 'headers' not in kwargs and 'auth' not in kwargs
        return Response({'code':200,'order_books':self.markets} if url.endswith('orderBooks') else self.data,self.status)


@pytest.fixture
def history(tmp_path):
    return LighterHistory(FakeAgent(tmp_path/'db.sqlite3'),session=Session(),clock=lambda:CUTOFF+100,sleep=lambda _:None)


def test_mapping_requires_id_symbol_perp_and_multiplier_and_preserves_zero(history):
    assert history.load(contract())['market_id']==0
    assert len(history.session.calls)==2
    assert history.session.calls[-1][1]['params']['end_timestamp']==CUTOFF-1
    assert history.load(contract())['cached'] is True
    assert len(history.session.calls)==2


@pytest.mark.parametrize('field,value',[('symbol','OTHER'),('market_type','spot'),('multiplier','1000'),('is_frozen',True),('status','inactive'),('market_id',1)])
def test_ambiguous_or_different_market_is_not_used(history,field,value):
    history.session.markets[0][field]=value
    with pytest.raises(ValueError):history.load(contract())
    assert not any(url.endswith('candles') for url,_ in history.session.calls)


def test_429_stops_further_provider_requests(history):
    history.session.status=429
    with pytest.raises(ValueError):history.load(contract())
    with pytest.raises(ValueError):history.load(contract())
    assert len(history.session.calls)==1


def test_open_candle_and_zero_volume_omission(history):
    rows=bars(240)+[{'t':CUTOFF,'o':999.,'h':999.,'l':999.,'c':999.}]
    payload=raw(rows);payload['c'][0].pop('v');payload['c'][0].pop('V')
    result=parse_candles(payload,CUTOFF)
    assert len(result)==240 and result[-1]['c']==100 and result[0]['v']==result[0]['V']==0


@pytest.mark.parametrize('field,value',[('o',float('nan')),('h',99),('l',101),('v',-1),('c',0),('t',1)])
def test_bad_candles_rejected(field,value):
    data=raw(bars());data['c'][0][field]=value
    with pytest.raises(ValueError):parse_candles(data,CUTOFF)


def test_conflicting_duplicate_rejected():
    data=raw(bars());b=deepcopy(data['c'][0]);b.update(c=100.5);data['c'].append(b)
    with pytest.raises(ValueError):parse_candles(data,CUTOFF)


@pytest.mark.parametrize('issue',['gap','stale','short'])
def test_missing_hours_stale_or_short_history_not_accepted(history,issue):
    rows=bars()
    if issue=='gap':rows.pop(10)
    elif issue=='stale':rows.pop()
    else:rows=rows[-100:]
    history.session.data=raw(rows)
    with pytest.raises(ValueError):history.load(contract())


def test_flat_series_has_zero_returns_neutral_rsi_and_no_direction():
    m=indicator_series(bars())[-1]
    assert m['change_24h_pct']==m['change_7d_pct']==0
    assert m['rsi14']==50 and m['ema20']==m['ema50']==100
    assert technical_side(m)=='wait'


def test_exact_time_returns_and_daily_weekly_conflict():
    rows=bars()
    for i,b in enumerate(rows):b.update(c=100+i*.1,h=150)
    m=indicator_series(rows)[-1]
    assert m['change_24h_pct']==pytest.approx((147.9/145.5-1)*100)
    assert m['change_7d_pct']==pytest.approx((147.9/131.1-1)*100)
    m.update(rsi14=55)
    assert technical_side(m)=='long'
    m['change_7d_pct']=-1
    assert technical_side(m)=='wait'


def test_backtest_next_bar_entry_and_stop_first_when_both_levels_hit():
    rows=bars(245);series=indicator_series(rows)
    series[239].update(close=100,ema20=99,ema50=98,rsi14=55,change_24h_pct=1,change_7d_pct=2)
    rows[240].update(o=100,h=120,l=80,c=110)
    result=backtest(rows,series,3.5,10,.0009,.01)
    assert result['trades']==1 and result['total_r']==pytest.approx(-1)
    assert result['win_rate_pct']==0 and result['funding_included'] is False


def test_backtest_gap_can_lose_more_than_one_r():
    rows=bars(245);series=indicator_series(rows)
    series[239].update(close=100,ema20=99,ema50=98,rsi14=55,change_24h_pct=1,change_7d_pct=2)
    rows[241].update(o=90,h=91,l=89,c=90)
    result=backtest(rows,series,3.5,10,.0009,.01)
    assert result['trades']==1 and result['total_r'] < -1


def test_model_can_veto_but_cannot_invent_a_side_or_instrument(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3')
    a={'strategy_version':STRATEGY,'data_verified':True,'rule_side':'short','metrics':{},
       'backtest':{'trades':2,'average_r':1.,'max_drawdown_r':1.},'risk_notice':'experiment','reason':'Проверенные числа'}
    row={'contract_id':0,'assessment':a,'ai':{'side':'wait','reason':'pending'}}
    agent.local_request=lambda *args:{'message':{'content':json.dumps({'reviews':[{'contract_id':0,'decision':'review','reason':'checks_pass'}]})}}
    assert review_candidates(agent,[row])==1
    assert row['ai']['side']=='short'
    assert safe_signal({'analysis_version':3,'candidates':[row]})['candidates'][0]['ai']['side']=='short'
    row['ai']['side']='long'
    assert safe_signal({'analysis_version':3,'candidates':[row]})['candidates'][0]['ai']['side']=='wait'
    agent.local_request=lambda *args:{'message':{'content':json.dumps({'reviews':[{'contract_id':999,'decision':'review'}]})}}
    review_candidates(agent,[row])
    assert row['ai']['side']=='wait' and row['model_review']['status']=='unavailable'


def test_live_snapshot_never_uses_history_future_or_fake_oi(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3');service=WaltTrading(agent,history_factory=DemoHistory,news_factory=NoTradingNews)
    packet=service.signal();a=packet['candidates'][0]['assessment']
    assert packet['analysis_version']==3 and packet['history_available']==1
    assert a['data_verified'] and a['oi'] is None
    assert packet['candidates'][0]['ai']['side']=='wait'
    assert service.agent.prepares==service.agent.executions==0


def test_no_oi_change_inferred_without_comparable_observation(history):
    row={'contract_id':0,'observed':CUTOFF,'open_interest_usd':'100'}
    assert history.observation(row) is None
    row.update(observed=CUTOFF+24*HOUR,open_interest_usd='120')
    result=history.observation(row)
    assert result['change_pct']==pytest.approx(20) and result['unit']=='USD'


def test_changing_stop_requires_new_retrospective(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3');service=WaltTrading(agent,history_factory=DemoHistory,news_factory=NoTradingNews)
    packet=service.signal();limits=service.limits();service.save_limits({'stop_pct':'4'},limits['revision'])
    with pytest.raises(ValueError,match='ретроспективы'):service.quote(packet['id'],0,'long')
    assert agent.prepares==agent.executions==0


def test_missing_model_reviews_fail_closed(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3')
    row={'contract_id':0,'assessment':{'rule_side':'long','metrics':{},'backtest':{'trades':1,'average_r':1,'max_drawdown_r':0},'risk_notice':'experiment'},'ai':{'side':'wait'}}
    agent.local_request=lambda *args:{'message':{'content':'{"reviews":[]}'}}
    review_candidates(agent,[row])
    assert row['ai']['side']=='wait' and row['model_review']['status']=='unavailable'


def test_market_review_does_not_receive_personal_or_simulated_trade_statistics(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3');inputs=[]
    def model(path,payload):
        inputs.append(payload['messages'][1]['content'])
        return {'message':{'content':'{"reviews":[{"contract_id":0,"decision":"review","reason":"checks_pass"}]}'}}
    agent.local_request=model
    for count in (0,100):
        row={'contract_id':0,'symbol':'BTC','assessment':{'rule_side':'long','metrics':{'rsi14':55},
            'backtest':{'trades':count,'average_r':-10},'risk_notice':'Small backtest sample',
            'checks':[{'key':'retrospective','ok':True},{'key':'funding','ok':True}],'reason':'Рыночные показатели проверены'},'ai':{'side':'wait'}}
        review_candidates(agent,[row])
        assert row['ai']['side']=='long'
    assert inputs[0]==inputs[1]
    assert 'backtest' not in inputs[0] and 'retrospective' not in inputs[0] and 'risk_notice' not in inputs[0]
    assert agent.executions==agent.prepares==0


def test_small_sample_is_not_accepted_as_a_market_review_reason(tmp_path):
    agent=FakeAgent(tmp_path/'db.sqlite3')
    row={'contract_id':0,'assessment':{'rule_side':'long','metrics':{},'reason':'candidate'},'ai':{'side':'wait'}}
    agent.local_request=lambda *args:{'message':{'content':'{"reviews":[{"contract_id":0,"decision":"wait","reason":"short_sample"}]}'}}
    review_candidates(agent,[row])
    assert row['model_review']['status']=='unavailable'
    assert 'статистики' not in row['ai']['reason']
