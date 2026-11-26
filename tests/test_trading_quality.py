from datetime import datetime,timedelta,timezone
from copy import deepcopy
import pytest
from invest_blog.trading_quality import record_close,strong_trend,checked_explanation,walt_entry_block,WALT_VERSION
from invest_blog.trading_rules_v2 import entry_block,VERSION
NOW=datetime(2026,11,26,10,15,tzinfo=timezone.utc)

def test_second_stop_has_four_hour_pause_and_profit_resets_streak():
    s={};t=NOW.timestamp();record_close(s,'x',t,True)
    assert s['cooldowns']['x']==t+3600
    record_close(s,'x',t+4000,True)
    assert s['cooldowns']['x']==t+4000+14400
    record_close(s,'x',t+5000,False);record_close(s,'x',t+6000,True)
    assert s['cooldowns']['x']==t+9600
    record_close(s,'x',t+100000,True)
    assert len(s['stop_history']['x'])==1

@pytest.mark.parametrize('fast,slow,atr,valid',[(100.1,100,1,False),(100.2,100,1,True),(101,100,0,False),('nan',100,1,False),(None,100,1,False)])
def test_normalized_trend_quality(fast,slow,atr,valid):
    assert strong_trend(fast,slow,atr)==valid

def test_invented_model_comparisons_are_not_displayed_and_countertrend_entry_is_blocked():
    f={'last_close':'99','sma_fast':'100','sma_slow':'101','atr':'2'}
    p=checked_explanation({'action':'long','confidence':.9,'reason':'Цена выше 100000 и средних'},f,{'direction':'short','sma10':'100','sma30':'102','atr14':'2'})
    assert p['action']=='hold' and p['confidence']==0
    assert '100000' not in p['reason'] and 'закрытие 15м 99' in p['reason']

def test_futures_reentry_requires_fresh_bar_and_new_breakout():
    clock=NOW+timedelta(hours=2);s={'strategy_version':VERSION};record_close(s,'x',NOW.timestamp(),True)
    snap={'trend_1h':{'direction':'long','sma10':'110','sma30':'100','atr14':'2','candle_at':(clock-timedelta(hours=1)).isoformat()}}
    f={'last_close':'105','breakout_high':'106','candle_at':(clock-timedelta(minutes=15)).isoformat()}
    assert 'пробоя' in entry_block(s,'x',snap,clock,f,'long')
    f['last_close']='107';assert entry_block(s,'x',snap,clock,f,'long') is None
    f['candle_at']=(NOW-timedelta(minutes=15)).isoformat()
    assert 'после стопа' in entry_block(s,'x',snap,clock,f,'long')

def test_walt_reentry_and_macro_direction_are_independent_of_ai_acceptance():
    t=NOW.timestamp();s={'strategy_version':WALT_VERSION};record_close(s,'1',t,True)
    row={'market_id':1,'side':'long','metrics':{'close':110,'ema20':108,'ema50':100,'atr14':2},'cutoff':t+7200,'scenario':{'trend_1h':'up','trend_4h':'up','resistance':109,'support':90,'breakout_volume_confirmed':True}}
    assert walt_entry_block(s,row,t+100) is not None
    assert walt_entry_block(s,row,t+7200) is None
    row['scenario']['breakout_volume_confirmed']=False
    assert 'объёмом' in walt_entry_block(s,row,t+7200)
    row['scenario']['trend_4h']='down';assert '1ч и 4ч' in walt_entry_block(s,row,t+7200)
