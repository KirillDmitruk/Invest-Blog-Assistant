import json
from datetime import date
import pytest
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.iis_portfolio import view,prepare,ResearchAPI,classify,collect_profiles

TODAY=date(2026,11,25)
@pytest.fixture
def store(tmp_path):
    s=PortfolioStore(tmp_path/'portfolio.db');prepare(s)
    with s.connect() as db:
        for aid,total in [('iis',10000),('other',900000)]:
            db.execute('INSERT INTO portfolio_snapshots VALUES(?,?,?,?,?,?,?,?,?)',('2026-11-25',aid,str(total),'7000','2000','500','500','0','2026-11-25T10:00:00Z'))
        for uid,kind,qty,price,currency,nkd in [('bond','bond','7','990','RUB','10'),('stock','share','2','1000','RUB',None),('TGLD@','etf','10','50','RUB',None),('RUB','currency','500','1','RUB',None)]:
            db.execute('INSERT INTO position_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',('2026-11-25','iis',uid,uid,uid,kind,qty,price,currency,nkd,None))
        db.execute('INSERT INTO position_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',('2026-11-25','other','foreign','foreign','FOREIGN','share','900','1000','RUB',None,None))
    return s

def show(s,today=TODAY):return view(s,'iis',{'bonds':[]},today=today)
def test_full_account_weights_cash_gold_and_account_isolation(store):
    r=show(store);a={x['key']:x for x in r['allocation']}
    assert r['total']==10000 and r['counts']['assets']==4
    assert a['bonds']['weight_pct']==70 and a['shares']['weight_pct']==20
    assert a['gold']['weight_pct']==5 and a['cash']['weight_pct']==5
    assert r['rebalance'][0]['gap_value']==500
    assert next(x for x in r['assets'] if x['id']=='bond')['market_value']==7000
    assert r['reconciliation_gap']==0

def test_gold_miner_is_stock_unknown_fund_is_other():
    p={'kind':'share','ticker':'PLZL'}
    assert classify(p,{'isin':'RU000A101X50'})[0]=='shares'
    assert classify({'kind':'etf','ticker':'TSAV'},{})[0]=='other'

def test_missing_fx_and_stale_do_not_propose_precise_rebalancing(store):
    with store.connect() as db:db.execute("UPDATE position_snapshots SET currency='USD' WHERE instrument_id='stock'")
    r=show(store);assert r['unvalued_count']==1 and not r['rebalance']
    assert next(a for a in r['assets'] if a['id']=='stock')['market_value'] is None
    old=show(store,date(2026,12,1));assert old['stale'] and not old['rebalance']
    assert all(a['action']=='verify' for a in old['assets'])

def test_fundamentals_zero_unknown_and_earnings_loss_explained(store):
    with store.connect() as db:
        db.execute('INSERT INTO iis_asset_profiles VALUES(?,?,?)',('stock',json.dumps({'uid':'stock','assetUid':'asset','sector':'financial','fundamentals':{'netIncomeTtm':-100,'netDebtToEbitda':8,'revenueTtm':0}}),'2026-11-25T10:00:00Z'))
    a=next(a for a in show(store)['assets'] if a['id']=='stock')
    assert a['action']=='reduce' and any('убыток' in s for s in a['reasons'])
    assert not any('выше 4' in s for s in a['reasons'])
    assert not any(f['key']=='revenueTtm' for f in a['fundamentals'])
    assert any('отчётного периода' in s for s in a['gaps'])

def test_read_only_gateway_blocks_orders_before_network():
    from zoneinfo import ZoneInfo
    api=ResearchAPI('test',ZoneInfo('Europe/Moscow'))
    with pytest.raises(ValueError,match='только чтение'):api.call('OrdersService','PostOrder',{})

def test_duplicate_fundamentals_not_partially_saved(store):
    class API:
        def call(self,service,method,payload):
            if method=='GetAssetFundamentals':return {'fundamentals':[{'assetUid':'asset','netIncomeTtm':12}]*2}
            return {'instrument':{'uid':payload['id'],'assetUid':'asset' if method=='ShareBy' else 'gold'}}
    assert collect_profiles(API(),store,'iis',TODAY)
    with store.connect() as db:r=json.loads(db.execute("SELECT payload FROM iis_asset_profiles WHERE uid='stock'").fetchone()[0])
    assert 'fundamentals' not in r

def test_partial_profit_data_does_not_become_hold(store):
    with store.connect() as db:
        db.execute('INSERT INTO iis_asset_profiles VALUES(?,?,?)',('stock',json.dumps({'uid':'stock','assetUid':'a','fundamentals':{'netIncomeTtm':0,'peRatioTtm':4}}),'2026-11-25T10:00:00Z'))
    a=next(a for a in view(store,'iis',{'policy':{'issuer_limit_pct':30}},today=TODAY)['assets'] if a['id']=='stock')
    assert a['action']=='verify'
    assert any('Прибыль компании не подтверждена' in s for s in a['gaps'])

def test_profiles_do_not_merge_similar_names_without_confirmed_identity(store):
    with store.connect() as db:
        db.execute("UPDATE position_snapshots SET name='Same name' WHERE account_id='iis'")
    assert show(store)['companies']==[]

def test_negative_cash_and_zero_account_are_not_normalized_away(store):
    with store.connect() as db:db.execute("UPDATE portfolio_snapshots SET cash_rub='-500',total_rub='0' WHERE account_id='iis'")
    r=show(store)
    assert r['cash']==-500 and not r['rebalance']
    assert all(a['weight_pct'] is None for a in r['assets'])
    assert all(a['action']=='verify' for a in r['assets'])
