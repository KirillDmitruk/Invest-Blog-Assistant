from datetime import datetime,timedelta,timezone
import copy,json
from decimal import Decimal
import pytest
from invest_blog.trading_rules_v2 import activate,hour_trend,VERSION
from invest_blog.tinvest_paper_cloud import seed
from invest_blog.paper_perps import initial,PaperEngine,activate_v2,num

NOW=datetime(2026,11,25,8,15,tzinfo=timezone.utc)

def hourly(down=False):
    rows=[]
    for i in range(40):
        n=200-i if down else 100+i
        q=lambda x:{'units':str(x),'nano':0}
        rows.append({'time':(NOW.replace(minute=0)-timedelta(hours=40-i)).isoformat(),'isComplete':True,
            'open':q(n),'close':q(n),'high':q(n+1),'low':q(n-1)})
    return rows

@pytest.mark.parametrize('down,expected',[(False,'long'),(True,'short')])
def test_closed_fresh_hourly_trend(down,expected):
    assert hour_trend(hourly(down),NOW)['direction']==expected

def test_hourly_future_incomplete_or_stale_not_accepted():
    with pytest.raises(ValueError):hour_trend(hourly()[:10],NOW)
    with pytest.raises(ValueError):hour_trend(hourly(),NOW+timedelta(hours=4))
    rows=hourly();extra=copy.deepcopy(rows[-1]);extra.update(time=NOW.isoformat(),isComplete=False)
    assert hour_trend(rows+[extra],NOW)==hour_trend(rows,NOW)

def test_migration_never_resets_cash_or_ledger(tmp_path):
    lab=seed(tmp_path/'a.db')
    with lab.db() as db:
        s=lab._load(db);s['cash']='987654';lab._save(db,s)
    assert activate(lab,NOW) and not activate(lab,NOW+timedelta(hours=1))
    state=lab.overview()['state'];assert state['cash']=='987654' and state['budget']=='1000000'
    assert len(state['strategy_changes'])==1 and state['strategy_baseline']['cash']=='987654'

def test_cooldown_and_hourly_filter_cannot_be_overridden(tmp_path):
    lab=seed(tmp_path/'a.db');activate(lab,NOW)
    c={'uid':'x','ticker':'X','tick':'1','tick_value':'1','margin_long':'500','margin_short':'500','expires_at':(NOW+timedelta(days=30)).isoformat()}
    snap={'contract':c,'price':'140','quoted_at':NOW.isoformat(),'tradeable':True,'trend_1h':hour_trend(hourly(),NOW)}
    with lab.db() as db:
        s=lab._load(db);s['watchlist']={'x':c}
        assert 'часовым трендом' in lab._open(db,s,'x',snap,{'atr':'2'},'short',NOW)
        assert 'Открыта' in lab._open(db,s,'x',snap,{'atr':'2'},'long',NOW)
        lab._close(db,s,'x',snap,'Стоп',NOW)
        assert 'часовая пауза' in lab._open(db,s,'x',snap,{'atr':'2'},'long',NOW+timedelta(minutes=59))
        later=NOW+timedelta(hours=1);snap['trend_1h']['candle_at']=(later-timedelta(hours=1,minutes=15)).isoformat()
        assert 'Открыта' in lab._open(db,s,'x',snap,{'atr':'2'},'long',later)

def test_walt_upgrade_keeps_existing_positions_and_history():
    state=initial();state['cash']='9900';state['positions']={'1':{'side':'short'}}
    old=copy.deepcopy(state['positions']);assert activate_v2(state,NOW.timestamp());assert not activate_v2(state,NOW.timestamp())
    assert state['positions']==old and state['cash']=='9900' and state['limits']['max_same_side']==2

def test_walt_same_side_count_and_risk_caps():
    state=initial();activate_v2(state,NOW.timestamp());e=PaperEngine(state)
    def open_(ident,side):
        return e.open({'market_id':ident,'symbol':'X'+str(ident),'side':side},
            {'symbol':'X'+str(ident),'price':'100','at':NOW.timestamp(),'active':True,'max_leverage':3,'step':'.001','min_quantity':'.001','min_notional':'1','maintenance':'.03'},'report',NOW.timestamp())
    # Give each asset its own group so this test isolates the direction rule.
    import invest_blog.paper_perps as module
    original=module.group
    try:
        module.group=lambda s:s
        assert open_(1,'short') and open_(2,'short')
        assert not open_(3,'short')
        assert open_(4,'long')
        for p in e.state['positions'].values():
            if p['side']=='short':p['risk']='200'
        e.state['limits']['max_same_side']=4
        assert not open_(5,'short') # Existing same-direction risk already exceeds 2.5%.
    finally:module.group=original
