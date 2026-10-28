"""Historical NAV is indicative, separate from broker-confirmed snapshots."""
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.history import HistoricalBackfill
from invest_blog.dashboard import create_app


def m(v,cur='RUB'):
    return {'units':str(v),'nano':0,'currency':cur}


def op(id,day,kind,amt,uid=None,qty=None,cur='RUB'):
    o={'id':id,'date':f'{day}T08:00:00+00:00','type':kind,'payment':m(amt,cur)}
    if uid: o['instrumentUid']=uid
    if qty is not None:o['quantityDone']=qty
    return o


class Market:
    def instrument(self,uid='',figi=''):
        return Instrument(uid or figi,'','Тестовая акция','', 'TEST','share','RUB',{})
    def daily_candles(self,uid,start,end):
        assert uid=='S1'
        return [{'time':'2026-10-27T00:00:00Z','close':m(95), 'isComplete':True,'priceCurrency':'RUB'},
                {'time':'2026-10-26T00:00:00Z','close':m(90), 'isComplete':True,'priceCurrency':'RUB'}]
    def bond(self,uid):
        raise AssertionError('No bond')


def populate(db, *, include_ops=True, currency='RUB'):
    portfolio={'totalAmountPortfolio':m(150), 'totalAmountBonds':m(0),
        'totalAmountShares':m(100), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(50)}
    position={'id':'S1', 'name':'Test', 'ticker':'TEST','kind':'share', 'quantity':'1',
              'price':'100','currency':currency,'nkd':None,'maturity':None}
    operations = [
        op('coupon','2026-10-28','OPERATION_TYPE_COUPON',5,'S1'),
        op('buy','2026-10-27','OPERATION_TYPE_BUY',-100,'S1',1),
        op('deposit','2026-10-26','OPERATION_TYPE_INPUT',100),
    ] if include_ops else []
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,
                                    'positions':[position]}], [('a',o) for o in operations],[],
                  coverage_ranges=[('a',date(2026,10,25),date(2026,10,28))])


def test_reconstructs_past_with_cash_and_historic_prices(tmp_path):
    db=PortfolioStore(tmp_path/'x.db');populate(db)
    report=HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert report['points']==3
    graph=db.get_dashboard_data()
    assert graph['history']==[{'day':'2026-10-28','total':150.0,'source':'snapshot'}]
    history={r['day']:r['total'] for r in graph['history_estimated']}
    assert history=={'2026-10-25':45.,'2026-10-26':145.,'2026-10-27':140.}
    assert graph['history_backfill']['points']==3
    page=TestClient(create_app(db.path)).get('/api/overview').json()
    assert page['history_estimated']==graph['history_estimated']
    html=TestClient(create_app(db.path)).get('/').text
    assert 'Расчётная история' in html and 'Снимки брокера' in html


def test_reconstruction_refuses_missing_operation_coverage(tmp_path):
    db=PortfolioStore(tmp_path/'x.db');populate(db)
    with pytest.raises(ValueError,match='Не хватает сплошной истории'):
        HistoricalBackfill(Market(),('a',),db).run(days=10)
    assert db.get_dashboard_data()['history_estimated']==[]


def test_missing_quote_not_zero_and_does_not_connect_across_gap(tmp_path):
    class Missing(Market):
        def daily_candles(self,uid,start,end):return []
    db=PortfolioStore(tmp_path/'x.db');populate(db)
    report=HistoricalBackfill(Missing(),('a',),db).run(days=4)
    assert report['points']==2
    graph=db.get_dashboard_data()
    assert [x['day'] for x in graph['history_estimated']]==['2026-10-25','2026-10-26']
    assert graph['history_backfill']['warnings']


def test_foreign_security_rejected_without_invented_fx(tmp_path):
    db=PortfolioStore(tmp_path/'x.db');populate(db,currency='USD')
    with pytest.raises(ValueError,match='FX'):
        HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert db.get_dashboard_data()['history_estimated']==[]


def test_transfer_operation_is_not_ignored(tmp_path):
    db=PortfolioStore(tmp_path/'x.db');populate(db)
    new=op('transfer','2026-10-27','OPERATION_TYPE_INPUT_SECURITIES',0,'S1')
    db.save_batch(date(2026,10,28),[],[('a',new)],[])
    with pytest.raises(ValueError,match='отдельного учёта'):
        HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert db.get_dashboard_data()['history_estimated']==[]


def test_last_good_backfill_survives_api_failure(tmp_path):
    db=PortfolioStore(tmp_path/'x.db');populate(db)
    runner=HistoricalBackfill(Market(),('a',),db)
    runner.run(days=4)
    previous=db.get_dashboard_data()['history_estimated']
    class Broken(Market):
        def daily_candles(self,uid,start,end):
            raise RuntimeError('Network is offline')
    with pytest.raises(RuntimeError):
        HistoricalBackfill(Broken(),('a',),db).run(days=4)
    assert db.get_dashboard_data()['history_estimated']==previous


def test_two_accounts_total_requires_both_points(tmp_path):
    db=PortfolioStore(tmp_path/'multi.db')
    populate(db)
    cash={'totalAmountPortfolio':m(50), 'totalAmountBonds':m(0),
          'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(50)}
    db.save_batch(date(2026,10,28),[{'account_id':'b','portfolio':cash,'positions':[]}],[],[],
                  coverage_ranges=[('b',date(2026,10,25),date(2026,10,28))])
    result=HistoricalBackfill(Market(),('a','b'),db).run(days=4)
    assert result['points']==6
    graph=db.get_dashboard_data()
    assert graph['total_rub']==200
    assert {x['day']:x['total'] for x in graph['history_estimated']}=={
        '2026-10-25':95, '2026-10-26':195, '2026-10-27':190}


def test_bond_price_uses_percent_of_nominal(tmp_path):
    db=PortfolioStore(tmp_path/'bonds.db')
    portfolio={'totalAmountPortfolio':m(1000), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(0)}
    pos={'id':'B1','name':'Bond','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],[],[],
                  coverage_ranges=[('a',date(2026,10,27),date(2026,10,28))])
    class Bonds:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Bond','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':m(1000),'amortizationFlag':False}
        def daily_candles(self,uid,start,end):
            return [{'time':'2026-10-27T00:00:00Z','close':m(98),'isComplete':True,
                     'priceCurrency':'PT.'}]
    HistoricalBackfill(Bonds(),('a',),db).run(days=2)
    assert db.get_dashboard_data()['history_estimated'][0]['total']==980.0


def _add_currency_position(db, *, ticker, name, price='1', quoted='RUB', uid=None):
    """Add a realistic GetPortfolio currency line beside an existing stock."""
    p = {'totalAmountPortfolio': m(150), 'totalAmountBonds': m(0),
         'totalAmountShares': m(100), 'totalAmountEtf': m(0),
         'totalAmountCurrencies': m(50)}
    stock={'id':'S1','name':'Test','ticker':'TEST','kind':'share',
           'quantity':'1','price':'100','currency':'RUB','nkd':None,'maturity':None}
    currency={'id':uid or ticker,'name':name,'ticker':ticker,'kind':'currency',
              'quantity':'50','price':price,'currency':quoted,'nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[
        {'account_id':'a','portfolio':p,'positions':[stock,currency]}
    ],[],[])


def test_rub_cash_position_is_not_double_counted(tmp_path):
    db=PortfolioStore(tmp_path/'rub.db')
    populate(db)
    _add_currency_position(db,ticker='RUB000UTSTOM',name='Российский рубль')
    result=HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert result['points']==3
    # RUB 50 already sits in cash_rub; adding quantity*1 again would double it.
    estimated={x['day']:x['total'] for x in db.get_dashboard_data()['history_estimated']}
    assert estimated=={'2026-10-25':45.,'2026-10-26':145.,'2026-10-27':140.}


def test_rub_cash_can_be_recognized_by_name_with_uid(tmp_path):
    db=PortfolioStore(tmp_path/'rub_uid.db')
    populate(db)
    _add_currency_position(db,ticker='',name='Российский рубль',uid='some-opaque-uid')
    result=HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert result['points']==3


@pytest.mark.parametrize('ticker,name,price',[
    ('USD000UTSTOM','Доллар США','90'),
    ('CNYRUB_TOM','Китайский юань','13'),
    ('RUB000UTSTOM','Российский рубль','90'),  # Wrong quote: not verified RUB cash.
    ('UNKNOWN','Неизвестная валюта','1'),
])
def test_foreign_or_unrecognized_cash_needs_fx(tmp_path,ticker,name,price):
    db=PortfolioStore(tmp_path/'fx.db')
    populate(db)
    _add_currency_position(db,ticker=ticker,name=name,price=price)
    with pytest.raises(ValueError,match='FX-курсов'):
        HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert db.get_dashboard_data()['history_estimated']==[]

@pytest.mark.parametrize('current', [None, m(0)])
def test_zero_or_missing_nominal_falls_back_to_initial_on_plain_bond(tmp_path, current):
    db=PortfolioStore(tmp_path/'nominal_fallback.db')
    portfolio={'totalAmountPortfolio':m(1000), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(0)}
    pos={'id':'B1','name':'Завершённый выпуск','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],[],[],
                  coverage_ranges=[('a',date(2026,10,27),date(2026,10,28))])
    class Bonds:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Завершённый выпуск','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':current,'initialNominal':m(1000), 'amortizationFlag':False}
        def daily_candles(self,uid,start,end):
            return [{'time':'2026-10-27T00:00:00Z','close':m(98),'isComplete':True,
                     'priceCurrency':'RUB'}]
    report=HistoricalBackfill(Bonds(),('a',),db).run(days=2)
    assert report['points']==1
    assert db.get_dashboard_data()['history_estimated'][0]['total']==980.0
    assert any('initialNominal' in w for w in report['warnings'])


def test_zero_nominal_amortized_does_not_invent_value_or_erase_previous(tmp_path):
    db=PortfolioStore(tmp_path/'zero_amort.db')
    portfolio={'totalAmountPortfolio':m(1000), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(0)}
    pos={'id':'B1','name':'Амортизируемый выпуск','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],[],[],
                  coverage_ranges=[('a',date(2026,10,27),date(2026,10,28))])
    class Bonds:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Амортизируемый выпуск','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':m(0), 'initialNominal':m(1000), 'amortizationFlag':True}
        def daily_candles(self,uid,start,end):
            pytest.fail('Не запрашиваем неиспользуемые свечи')
    with db.connect() as connection:
        connection.execute("INSERT INTO reconstructed_portfolio_history VALUES (?,?,?,?,?)",
                           ('2026-10-27','a','777','estimated','2026-10-28T00:00:00Z'))
    with pytest.raises(ValueError,match='не удалось|Не удалось') as exc:
        HistoricalBackfill(Bonds(),('a',),db).run(days=2)
    assert 'B' in str(exc.value)
    assert db.get_dashboard_data()['history_estimated'][0]['total']==777


def test_cash_only_days_survive_unknown_bond_nominal(tmp_path):
    db=PortfolioStore(tmp_path/'partial.db')
    portfolio={'totalAmountPortfolio':m(1050), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(50)}
    pos={'id':'B1','name':'Облигация без номинала','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    # Bought on the 27th. We can recover 25, 26 (zero bonds), but not 27.
    ops=[op('buy','2026-10-27','OPERATION_TYPE_BUY',-1000,'B1',1)]
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],
                  [('a',o) for o in ops],[],
                  coverage_ranges=[('a',date(2026,10,25),date(2026,10,28))])
    class Missing:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Облигация без номинала','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':m(0),'initialNominal':m(1000),'amortizationFlag':True}
        def daily_candles(self,uid,start,end):
            pytest.fail('Не запрашиваем неиспользуемые свечи')
    report=HistoricalBackfill(Missing(),('a',),db).run(days=4)
    assert report['points']==2
    assert report['days_missing']==1
    assert {r['day']:r['total'] for r in db.get_dashboard_data()['history_estimated']}=={
        '2026-10-25':1050.,'2026-10-26':1050.}


def test_invalid_nominal_currency_never_treated_as_rub(tmp_path):
    from invest_blog.history import _resolve_bond_nominal
    nominal, reason=_resolve_bond_nominal({
        'nominal':m(1000,'USD'),'initialNominal':m(1000,'RUB'),'amortizationFlag':False})
    assert nominal is None
    assert 'валют' in reason
    nominal, reason=_resolve_bond_nominal({
        'nominal':None,'initialNominal':{'units':'1000','nano':0},'amortizationFlag':False})
    assert nominal is None
    assert 'валюта' in reason


@pytest.mark.parametrize('quote_unit', ['PT.', 'PT'])
def test_bond_candle_points_not_foreign_currency(tmp_path, quote_unit):
    db=PortfolioStore(tmp_path/'pt_bonds.db')
    portfolio={'totalAmountPortfolio':m(1000), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(0)}
    pos={'id':'B1','name':'Рублёвая облигация','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],[],[],
                  coverage_ranges=[('a',date(2026,10,27),date(2026,10,28))])
    class Bonds:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Рублёвая облигация','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':m(1000),'amortizationFlag':False}
        def daily_candles(self,uid,start,end):
            return [{'time':'2026-10-27T00:00:00Z','close':m(98),
                     'isComplete':True,'priceCurrency':quote_unit}]
    report=HistoricalBackfill(Bonds(),('a',),db).run(days=2)
    assert report['points']==1
    assert db.get_dashboard_data()['history_estimated'][0]['total']==980.0


def test_points_are_not_rubles_for_shares(tmp_path):
    db=PortfolioStore(tmp_path/'pt_shares.db');populate(db)
    class BadQuote(Market):
        def daily_candles(self,uid,start,end):
            candles=super().daily_candles(uid,start,end)
            for candle in candles:
                candle['priceCurrency']='PT.'
            return candles
    with pytest.raises(ValueError,match=r'PT\.'):
        HistoricalBackfill(BadQuote(),('a',),db).run(days=4)
    assert db.get_dashboard_data()['history_estimated']==[]


def test_unexpected_foreign_unit_bond_is_rejected(tmp_path):
    db=PortfolioStore(tmp_path/'usd_bonds.db')
    portfolio={'totalAmountPortfolio':m(1000), 'totalAmountBonds':m(1000),
               'totalAmountShares':m(0), 'totalAmountEtf':m(0), 'totalAmountCurrencies':m(0)}
    pos={'id':'B1','name':'Рублёвая облигация','ticker':'B','kind':'bond','quantity':'1',
         'price':'1000','currency':'RUB','nkd':None,'maturity':None}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':portfolio,'positions':[pos]}],[],[],
                  coverage_ranges=[('a',date(2026,10,27),date(2026,10,28))])
    class Bonds:
        def instrument(self,uid='',figi=''):
            return Instrument('B1','','Рублёвая облигация','','B','bond','RUB',{})
        def bond(self,uid):return {'nominal':m(1000), 'amortizationFlag':False}
        def daily_candles(self,uid,start,end):
            return [{'time':'2026-10-27T00:00:00Z','close':m(98),
                     'isComplete':True,'priceCurrency':'USD'}]
    with pytest.raises(ValueError,match='USD'):
        HistoricalBackfill(Bonds(),('a',),db).run(days=2)
    assert db.get_dashboard_data()['history_estimated']==[]
