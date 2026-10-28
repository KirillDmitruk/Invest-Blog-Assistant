from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3

from fastapi.testclient import TestClient

from invest_blog.bond_analytics import get_bond_analytics, monthly_cashflows
from invest_blog.dashboard import create_app
from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.sync import PortfolioSync
from invest_blog.tinvest import TInvestError


def m(v,c='RUB'):
    return {'units':str(v),'nano':0,'currency':c}


class BondApi:
    tz=ZoneInfo('Europe/Moscow')
    coupon_calls=0
    fail_coupons=False
    def __init__(self):
        self.coupon_calls=0
    def portfolio(self,account):
        qty=2 if account=='a' else 3
        return {'totalAmountPortfolio':m(10000),'totalAmountBonds':m(8000),
                'totalAmountShares':m(0),'totalAmountEtf':m(0),'totalAmountCurrencies':m(2000),
                'positions':[{'instrumentUid':'UID1','figi':'FIGI1',
                    'instrumentType':'bond','quantity':m(qty),'currentPrice':m(1000),
                    'currentNkd':m(6)}]}
    def instrument(self,uid='',figi=''):
        return Instrument('UID1','FIGI1','Облигация тест','RU123456789','TEST','bond','RUB',{})
    def bond(self,uid):
        assert uid=='UID1'
        return {'name':'Облигация тест','isin':'RU123456789','maturityDate':'2028-01-01T00:00:00Z',
                'nominal':m(1000),'couponQuantityPerYear':4,'floatingCouponFlag':True,
                'amortizationFlag':False,'perpetualFlag':False}
    def coupons(self,uid,start,end):
        self.coupon_calls+=1
        if self.fail_coupons:raise TInvestError('API unavailable')
        assert start==date(2026,10,28)
        return [
            {'couponDate':'2026-11-05T00:00:00Z','couponNumber':7,'payOneBond':m(20),
             'couponType':'COUPON_TYPE_FLOATING'},
            {'couponDate':'2027-02-05T00:00:00Z','couponNumber':8,'payOneBond':m(0),
             'couponType':'COUPON_TYPE_FLOATING'},
            {'couponDate':'2027-10-30T00:00:00Z','couponNumber':10,'payOneBond':m(30)}]
    def operations(self,account,start,end):
        return [
            {'id':f'{account}-x1','date':'2026-10-02T06:00:00Z',
             'type':'OPERATION_TYPE_COUPON','payment':m(33),'state':'OPERATION_STATE_EXECUTED',
             'instrumentUid':'UID1'},
            {'id':f'{account}-x2','date':'2026-09-12T06:00:00Z',
             'type':'OPERATION_TYPE_INPUT','payment':m(1000),'state':'OPERATION_STATE_EXECUTED'},
        ]


def test_bond_sync_calendar_aggregates_accounts_and_discloses_unknown(tmp_path):
    db=PortfolioStore(tmp_path/'one.sqlite3')
    api=BondApi()
    result=PortfolioSync(api,('a','b'),db).run(date(2026,10,28),365)
    assert result['coupon_calendars_updated']==1 and api.coupon_calls==1
    data=get_bond_analytics(db)
    assert data['bond_count']==1
    assert data['bonds'][0]['quantity']==5
    assert data['bonds'][0]['nominal']==1000
    assert data['bonds'][0]['floating'] is True
    assert data['calendar']['known_total']['RUB']==100
    assert data['calendar']['unknown_amount_events']==1
    assert len(data['calendar']['events'])==2  # last coupon is outside 12 calendar months
    assert data['calendar']['events'][1]['amount'] is None
    income=data['actual_income']['months']
    assert next(x for x in income if x['month']=='2026-10')['coupons']['RUB']==66
    assert next(x for x in income if x['month']=='2026-09')['deposits']['RUB']==2000
    assert next(x for x in income if x['month']=='2026-10')['status']=='partial'
    assert next(x for x in income if x['month']=='2026-09')['status']=='complete'
    assert next(x for x in income if x['month']=='2025-11')['status']=='complete'


def test_second_same_day_sync_uses_cached_calendar(tmp_path):
    api=BondApi(); db=PortfolioStore(tmp_path/'s.db')
    sync=PortfolioSync(api,('a',),db)
    sync.run(date(2026,10,28),365)
    sync.run(date(2026,10,28),365)
    assert api.coupon_calls==1
    assert get_bond_analytics(db)['calendar']['known_total']=={'RUB':40.0}


def test_api_calendar_failure_does_not_invent_zero(tmp_path):
    api=BondApi(); api.fail_coupons=True
    db=PortfolioStore(tmp_path/'e.db')
    result=PortfolioSync(api,('a',),db).run(date(2026,10,28),365)
    assert any('GetBondCoupons' in w for w in result['warnings'])
    payload=get_bond_analytics(db)['calendar']
    assert payload['missing_bonds']==['Облигация тест']
    assert payload['covered_bonds']==0
    assert payload['known_total']=={}


def test_stale_calendar_values_excluded(tmp_path):
    db=PortfolioStore(tmp_path/'s.db')
    PortfolioSync(BondApi(),('a',),db).run(date(2026,10,28),365)
    with db.connect() as con:
        con.execute("UPDATE bond_calendar_refresh SET fetched_on='2026-10-27'")
    payload=get_bond_analytics(db)['calendar']
    assert payload['known_total']=={} and payload['stale_bonds']
    assert payload['covered_bonds']==0


def test_v11_database_migrates_and_dashboard_serves_new_data(tmp_path):
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE example_existing_table (value TEXT)')
        con.execute("INSERT INTO example_existing_table VALUES ('preserved')")
    db=PortfolioStore(path)
    with db.connect() as con:
        assert con.execute('SELECT value FROM example_existing_table').fetchone()[0]=='preserved'
        assert con.execute("SELECT name FROM sqlite_master WHERE name='bond_coupon_events'").fetchone()
    PortfolioSync(BondApi(),('a',),db).run(date(2026,10,28),365)
    client=TestClient(create_app(path))
    res=client.get('/api/bonds')
    assert res.status_code==200 and res.json()['bond_count']==1
    assert client.post('/api/bonds').status_code==405


def test_empty_portfolio_has_no_made_up_future(tmp_path):
    db=PortfolioStore(tmp_path/'a.sqlite3')
    assert get_bond_analytics(db)['has_data'] is False


def test_uncovered_months_not_reported_as_confirmed_zero(tmp_path):
    db=PortfolioStore(tmp_path/'u.db')
    PortfolioSync(BondApi(),('a',),db).run(date(2026,10,28),90)
    months=monthly_cashflows(db,date(2026,10,28))['months']
    assert next(x for x in months if x['month']=='2026-01')['status']=='no_coverage'


def test_income_excludes_accounts_not_in_latest_snapshot(tmp_path):
    db=PortfolioStore(tmp_path/'history.db')
    p={'totalAmountPortfolio':m(1),'totalAmountBonds':m(0),
       'totalAmountShares':m(0),'totalAmountCurrencies':m(1),'totalAmountEtf':m(0)}
    old={'id':'old','date':'2026-10-20T12:00:00+03:00',
         'type':'OPERATION_TYPE_COUPON','payment':m(900)}
    new={'id':'new','date':'2026-10-21T12:00:00+03:00',
         'type':'OPERATION_TYPE_COUPON','payment':m(10)}
    db.save_batch(date(2026,10,27),[{'account_id':'closed','portfolio':p,'positions':[]}],
                  [('closed',old)],[],coverage_ranges=[('closed',date(2026,10,1),date(2026,10,27))])
    db.save_batch(date(2026,10,28),[{'account_id':'open','portfolio':p,'positions':[]}],
                  [('open',new)],[],coverage_ranges=[('open',date(2026,10,1),date(2026,10,28))])
    october=monthly_cashflows(db,date(2026,10,28))['months'][-1]
    assert october['coupons']['RUB']==10


def test_coupon_without_identifier_does_not_overwrite_old_calendar(tmp_path):
    db=PortfolioStore(tmp_path/'protected.db')
    PortfolioSync(BondApi(),('a',),db).run(date(2026,10,28),365)
    class BrokenSchedule(BondApi):
        def coupons(self,uid,start,end):
            return [{'couponDate':'2026-11-05T00:00:00Z','payOneBond':m(100)}]
    # Force a refresh by removing freshness, preserving previous events.
    with db.connect() as con:
        con.execute("UPDATE bond_calendar_refresh SET fetched_on='2026-10-01'")
    report=PortfolioSync(BrokenSchedule(),('a',),db).run(date(2026,10,28),365)
    assert any('GetBondCoupons' in note for note in report['warnings'])
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM bond_coupon_events').fetchone()[0]==3


def test_import_deduplicates_stable_operation_id_when_cursor_changes(tmp_path):
    store = PortfolioStore(tmp_path / 'dedup.sqlite3')
    portfolio = {'totalAmountPortfolio': m(100), 'totalAmountBonds': m(0),
                 'totalAmountShares': m(0), 'totalAmountEtf': m(0),
                 'totalAmountCurrencies': m(100)}
    account = {'account_id': 'a', 'portfolio': portfolio, 'positions': []}
    operation = {'id': 'op-42', 'cursor': 'cursor-1', 'date': '2026-10-28T11:00:00Z',
                 'type': 'OPERATION_TYPE_COUPON', 'payment': m(25)}
    store.save_batch(date(2026, 10, 28), [account], [('a', operation)], [])
    store.save_batch(date(2026, 10, 28), [account],
                     [('a', {**operation, 'cursor': 'cursor-2'})], [])
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM broker_operations').fetchone()[0] == 1
