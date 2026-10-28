"""GetBondEvents: broker's principal payment per bond can verify full-redemption units."""
from datetime import date
from decimal import Decimal

import pytest

from invest_blog.history import _event_redemption_count, HistoricalBackfill
from invest_blog.tinvest import TInvest
from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore


def money(v, currency='RUB'):
    return {'units':str(v), 'nano':0, 'currency':currency}


def event(v, when='2026-10-15', currency='RUB', number=1):
    return {'eventType':'EVENT_TYPE_MTY', 'eventNumber':number,
            'payDate':f'{when}T00:00:00Z', 'payOneBond':money(v,currency)}


def test_event_uses_per_bond_principal_for_amortized_bond():
    result = _event_redemption_count(Decimal('1500'), date(2026,10,15), 'B1', [event(500)])
    assert result == Decimal(3)


def test_event_missing_or_wrong_currency_cannot_verify():
    assert _event_redemption_count(Decimal(1500),date(2026,10,15),'B1',[]) is None
    assert _event_redemption_count(Decimal(1500),date(2026,10,15),'B1',[event(500,'2026-10-15','USD')]) is None
    assert _event_redemption_count(Decimal(1500),date(2026,10,15),'B1',[event(500,'2026-09-01')]) is None


def test_event_noninteger_cash_ratio_not_guessed():
    assert _event_redemption_count(Decimal(1250),date(2026,10,15),'B1',[event(500)]) is None


def test_multiple_events_same_date_rejected():
    with pytest.raises(ValueError,match='несколько'):
        _event_redemption_count(Decimal(1500),date(2026,10,15),'B1',
                                [event(500,number=1), event(750,number=2)])


def test_event_priority_prefers_payment_date_nearest():
    e1=event(500,'2026-10-15',number=1)
    e2=event(750,'2026-10-12',number=2)
    assert _event_redemption_count(Decimal(1500),date(2026,10,15),'B1',[e1,e2]) == 3


def test_bond_events_rest_uses_mty_and_checks_response():
    calls=[]
    obj=object.__new__(TInvest)
    from zoneinfo import ZoneInfo
    obj.tz=ZoneInfo('Europe/Moscow')
    def fake_call(service,method,payload):
        calls.append((service,method,payload))
        return {'events':[event(500)]}
    obj.call=fake_call
    assert obj.bond_events('B1',date(2026,10,1),date(2026,10,31)) == [event(500)]
    assert calls[0][0:2] == ('InstrumentsService','GetBondEvents')
    assert calls[0][2]['type'] == 'EVENT_TYPE_MTY'
    assert calls[0][2]['instrumentId'] == 'B1'
    obj.call=lambda *_: {}
    with pytest.raises(Exception,match='events'):
        obj.bond_events('B1',date(2026,10,1),date(2026,10,31))


def test_backfill_full_amortized_redemption_does_not_invent_nominal(tmp_path):
    store=PortfolioStore(tmp_path/'x.sqlite3')
    p={'totalAmountPortfolio':money(1500),'totalAmountBonds':money(0),
       'totalAmountShares':money(0),'totalAmountEtf':money(0),
       'totalAmountCurrencies':money(1500)}
    op={'id':'repay1','date':'2026-10-15T10:00:00Z','type':'OPERATION_TYPE_BOND_REPAYMENT_FULL',
        'payment':money(1500),'instrumentUid':'B1'}
    store.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':p,'positions':[],
             'captured_at':'2026-10-28T12:00:00+00:00'}], [('a',op)],[],
             coverage_ranges=[('a',date(2026,10,1),date(2026,10,28))])
    class API:
        def instrument(self,uid='',figi=''):
            return Instrument(uid or figi,'','B','','B','bond','RUB',{})
        def bond(self,uid):
            return {'nominal':money(0),'initialNominal':money(1000),
                    'amortizationFlag':True}
        def bond_events(self,uid,start,end):
            assert uid == 'B1'
            return [event(500)]
        def daily_candles(self,uid,start,end):
            pytest.fail('Unknown amortized nominal cannot be priced')
    report=HistoricalBackfill(API(),('a',),store).run(days=28)
    assert report['points']>0
    assert report['days_missing']>0  # Bonds before maturity are NOT priced at an invented face.
    assert any('GetBondEvents' in x for x in report['warnings'])
    result={r['day']:r['total'] for r in store.get_dashboard_data()['history_estimated']}
    assert '2026-10-14' not in result
    assert result['2026-10-27']==1500.0
