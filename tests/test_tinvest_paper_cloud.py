from datetime import datetime,timedelta,timezone
from decimal import Decimal
import base64,json
import pytest
from invest_blog.tinvest_paper_cloud import seed,pack,validate_envelope,review
from invest_blog.trading_lab import TradingLab
from invest_blog.trading_market import FuturesMarket


def test_separate_million_account_and_roundtrip(tmp_path):
    old=TradingLab(tmp_path/'old.db')
    lab=seed(tmp_path/'new.db');value=lab.overview()
    assert value['account']['equity']=='1000000.00'
    assert value['state']['risk']['max_positions']==4
    raw=validate_envelope(pack(lab));(tmp_path/'copy.db').write_bytes(raw)
    assert TradingLab(tmp_path/'copy.db').overview()['state']==value['state']
    assert old.overview()['account']['equity']=='10000.00'


@pytest.mark.parametrize('service,method',[('OrdersService','PostOrder'),('SandboxService','PostSandboxOrder'),('UsersService','GetAccounts')])
def test_broker_write_and_account_access_denied(service,method):
    class Never:
        def call(self,*args):raise AssertionError('Transport must not be reached')
    with pytest.raises(ValueError):FuturesMarket('',None,api=Never()).read(service,method,{})


def test_wrong_currency_ledger_is_not_used(tmp_path):
    value=pack(seed(tmp_path/'new.db'));value['initial']='10000'
    with pytest.raises(ValueError):validate_envelope(value)


def test_minute_replay_stop_first_and_gap_fill_tick_value(tmp_path):
    lab=seed(tmp_path/'new.db');now=datetime(2026,11,23,10,tzinfo=timezone.utc)
    c={'uid':'x','ticker':'X','tick':'2','tick_value':'10','margin_long':'1000','margin_short':'1000','expires_at':(now+timedelta(days=30)).isoformat()}
    snap={'contract':c,'price':'100','quoted_at':now.isoformat(),'tradeable':True,'candles':[]}
    with lab.db() as db:
        s=lab._load(db);s['watchlist']={'x':c}
        lab._open(db,s,'x',snap,{'atr':'2'},'long',now);lab._save(db,s)
    p=lab.overview()['state']['positions']['x'];assert p['entry']=='102'
    later=now+timedelta(minutes=3)
    snap['protection_candles']=[{'time':(now+timedelta(minutes=1)).isoformat(),'open':'94','low':'90','high':'120','close':'100'}]
    with lab.db() as db:
        s=lab._load(db);lab._mark(db,s,{'x':snap},later);lab._save(db,s)
    value=lab.overview();t=value['trades'][0]
    assert not value['state']['positions'] and t['action']=='close'
    assert Decimal(t['detail']['price'])==92 # gap below stop, minus one 2-point tick
    assert Decimal(t['detail']['pnl_before_exit_fee'])==-500 # 10 contracts × 5 ticks × RUB10


def test_invalid_batch_does_not_become_a_trade(monkeypatch):
    monkeypatch.setenv('CF_ACCOUNT_ID','account');monkeypatch.setenv('CF_AI_TOKEN','fake')
    import invest_blog.tinvest_paper_cloud as module
    monkeypatch.setattr(module,'json_call',lambda *a,**k:{'result':{'response':'{"decisions":[]}'}})
    with pytest.raises(ValueError):review([{'uid':'a','features':{},'position':None}])


def test_cloud_monitor_never_reopens_or_reanalyzes_same_batch(tmp_path,monkeypatch):
    import invest_blog.tinvest_paper_cloud as module
    now=datetime(2026,11,23,10,tzinfo=timezone.utc);lab=seed(tmp_path/'cloud.db')
    c={'uid':'x','ticker':'TEST','tick':'1','tick_value':'5','margin_long':'500','margin_short':'500','expires_at':(now+timedelta(days=30)).isoformat()}
    bars=[{'time':(now-timedelta(minutes=(35-i)*15)).isoformat(),'open':str(100+i),'close':str(100+i),'high':str(101+i),'low':str(99+i)} for i in range(35)]
    class Market:
        analyze=True
        def universe(self,clock):return {'uids':['x'],'eligible_contracts':1,'selection':'test'}
        def snapshot(self,uid,clock=None):return {'contract':c,'price':'135','quoted_at':(clock or now).isoformat(),'tradeable':True,'candles':bars}
    class Store:
        saved=[]
        def save(self,value):self.saved.append(value)
    calls=[]
    def review(candidates):
        calls.append(candidates)
        return {'x':{'action':'long','confidence':.8,'reason':'Test rising trend'}}
    monkeypatch.setattr(module,'review',review)
    store=Store();module.cycle(lab,store,Market(),now)
    assert len(calls)==1 and len(lab.overview()['trades'])==1
    module.cycle(lab,store,Market(),now+timedelta(minutes=1))
    assert len(calls)==1 and len(lab.overview()['trades'])==1
    assert len(store.saved)==3 # Intent before AI, then two ledger checkpoints.
