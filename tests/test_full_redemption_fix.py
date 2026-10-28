"""Matured bonds: principal can verify a unit count only for plain RUB bonds."""
from datetime import date
from decimal import Decimal
import pytest

from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.history import HistoricalBackfill


def m(value, currency='RUB'):
    return {'units': str(value), 'nano': 0, 'currency': currency}


def fixture_db(tmp_path, amount='2000', redemption_id='B1'):
    store=PortfolioStore(tmp_path/'history.sqlite3')
    portfolio={
        'totalAmountPortfolio':m(int(amount)),
        'totalAmountBonds':m(0),
        'totalAmountShares':m(0),
        'totalAmountEtf':m(0),
        'totalAmountCurrencies':m(int(amount)),
    }
    store.save_batch(date(2026,10,28),[
        {'account_id':'a','portfolio':portfolio,'positions':[],
         'captured_at':'2026-10-28T12:00:00+00:00'}
    ], [('a',{'id':'repay','date':'2026-10-26T08:00:00+00:00',
              'type':'OPERATION_TYPE_BOND_REPAYMENT_FULL','payment':m(amount),
              'instrumentUid':redemption_id,'quantityDone':0})], [],
        coverage_ranges=[('a',date(2026,10,25),date(2026,10,28))])
    return store


class Bonds:
    def __init__(self, *, amortization=False, nominal=None, initial=None):
        self.amortization=amortization
        self.nominal=nominal if nominal is not None else m(0)
        self.initial=initial if initial is not None else m(1000)
        self.candles_called=0

    def instrument(self, uid='', figi=''):
        return Instrument(uid or figi,'','Погашенная облигация','','B','bond','RUB',{})

    def bond(self, uid):
        return {'nominal':self.nominal,'initialNominal':self.initial,
                'amortizationFlag':self.amortization}

    def daily_candles(self, uid, start, end):
        self.candles_called+=1
        assert uid=='B1'
        return [{'time':'2026-10-25T00:00:00Z', 'close':m(98),
                 'isComplete':True,'priceCurrency':'PT.'}]


def test_full_repayment_infers_only_exact_non_amortizing_count(tmp_path):
    store=fixture_db(tmp_path)
    source=Bonds()
    result=HistoricalBackfill(source,('a',),store).run(days=4)
    assert result['points']==3
    assert source.candles_called==1
    history={r['day']:r['total'] for r in store.get_dashboard_data()['history_estimated']}
    assert history=={'2026-10-25':1960.0, '2026-10-26':2000.0,'2026-10-27':2000.0}


def test_partial_redemption_without_credible_unit_stops_before_candles(tmp_path):
    store=fixture_db(tmp_path)
    source=Bonds(amortization=True)
    with pytest.raises(ValueError,match='без количества.*номинала'):
        HistoricalBackfill(source,('a',),store).run(days=4)
    assert source.candles_called==0
    report=HistoricalBackfill(source,('a',),store).run(days=4,partial=True)
    assert report['period'].startswith('2026-10-26')
    assert report['points']==2
    assert store.get_dashboard_data()['history_estimated']


def test_non_integer_principal_ratio_is_not_guessed(tmp_path):
    store=fixture_db(tmp_path,amount='1500')
    source=Bonds()
    with pytest.raises(ValueError,match='не кратно номиналу'):
        HistoricalBackfill(source,('a',),store).run(days=4)
    assert source.candles_called==0


def test_non_rub_nominal_is_not_guessed(tmp_path):
    store=fixture_db(tmp_path)
    source=Bonds(initial=m(1000,'USD'))
    with pytest.raises(ValueError,match='без количества'):
        HistoricalBackfill(source,('a',),store).run(days=4)
    assert source.candles_called==0


def test_full_repayment_explicit_positive_units_if_api_has_them(tmp_path):
    store=fixture_db(tmp_path,amount='2000')
    with store.connect() as db:
        row=db.execute("SELECT raw_json FROM broker_operations WHERE kind='OPERATION_TYPE_BOND_REPAYMENT_FULL'").fetchone()
        import json
        operation=json.loads(row['raw_json'])
        operation['quantityDone']=2
        db.execute("UPDATE broker_operations SET raw_json=? WHERE kind='OPERATION_TYPE_BOND_REPAYMENT_FULL'",(json.dumps(operation),))
    source=Bonds()
    result=HistoricalBackfill(source,('a',),store).run(days=4)
    assert result['points']==3
