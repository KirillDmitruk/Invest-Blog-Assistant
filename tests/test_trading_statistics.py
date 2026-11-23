from invest_blog.trading_lab import TradingLab
import json

def add(lab,at,uid,action,payload):
    with lab.db() as db:db.execute('INSERT INTO lab_trades(at,uid,ticker,action,payload) VALUES(?,?,?,?,?)',(at,uid,uid,action,json.dumps(payload)))

def test_close_day_moscow_and_both_fees(tmp_path):
    lab=TradingLab(tmp_path/'a.db')
    add(lab,'2026-11-22T12:00:00+00:00','a','open_long',{'fee':'4'})
    add(lab,'2026-11-22T22:00:00+00:00','a','close',{'pnl_before_exit_fee':'30','fee':'4'})
    add(lab,'2026-11-23T10:00:00+00:00','b','open_short',{'fee':'2'})
    add(lab,'2026-11-23T12:00:00+00:00','b','close',{'pnl_before_exit_fee':'-6','fee':'2'})
    add(lab,'2026-11-23T13:00:00+00:00','c','open_long',{'fee':'10'})
    r=lab.overview()['daily_results']
    assert r==[{'date':'2026-11-23','net_pnl':'12.00','closed':2,'wins':1,'losses':1,'flat':0,'unmatched':0}]

def test_missing_open_does_not_invent_profit(tmp_path):
    lab=TradingLab(tmp_path/'a.db')
    add(lab,'2026-11-23T12:00:00+00:00','a','close',{'pnl_before_exit_fee':'30','fee':'4'})
    r=lab.overview()['daily_results'][0]
    assert r['net_pnl'] is None and r['unmatched']==1

def test_empty_history_has_no_fake_days(tmp_path):
    assert TradingLab(tmp_path/'a.db').overview()['daily_results']==[]
