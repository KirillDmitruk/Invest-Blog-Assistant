"""Regression tests for backfill operation replay and safe partial history."""
from datetime import date
from zoneinfo import ZoneInfo
import pytest
from invest_blog.model import Instrument
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.history import HistoricalBackfill


def m(v, cur='RUB'):
    return {'units': str(v), 'nano': 0, 'currency': cur}


def op(id, day, kind, amt, uid=None, qty=None, cur='RUB'):
    value = {'id': id, 'date': f'{day}T08:00:00+00:00', 'type': kind,
             'payment': m(amt, cur)}
    if uid:
        value['instrumentUid'] = uid
    if qty is not None:
        value['quantityDone'] = qty
    return value


class Market:
    def instrument(self, uid='', figi=''):
        return Instrument(uid or figi, '', 'Тестовая акция', '', 'TEST', 'share', 'RUB', {})

    def daily_candles(self, uid, start, end):
        return [{'time': '2026-10-27T00:00:00Z', 'close': m(95),
                 'isComplete': True, 'priceCurrency': 'RUB'},
                {'time': '2026-10-26T00:00:00Z', 'close': m(90),
                 'isComplete': True, 'priceCurrency': 'RUB'}]

    def bond(self, uid):
        raise AssertionError('No bond metadata expected')


def populate(db, *, include_ops=False):
    portfolio = {'totalAmountPortfolio': m(150), 'totalAmountBonds': m(0),
                 'totalAmountShares': m(100), 'totalAmountEtf': m(0),
                 'totalAmountCurrencies': m(50)}
    position = {'id': 'S1', 'name': 'Test', 'ticker': 'TEST', 'kind': 'share',
                'quantity': '1', 'price': '100', 'currency': 'RUB',
                'nkd': None, 'maturity': None}
    operations = [op('coupon', '2026-10-28', 'OPERATION_TYPE_COUPON', 5, 'S1'),
                  op('buy', '2026-10-27', 'OPERATION_TYPE_BUY', -100, 'S1', 1),
                  op('deposit', '2026-10-26', 'OPERATION_TYPE_INPUT', 100)] if include_ops else []
    db.save_batch(date(2026, 10, 28),
                  [{'account_id': 'a', 'portfolio': portfolio, 'positions': [position]}],
                  [('a', o) for o in operations], [],
                  coverage_ranges=[('a', date(2026, 10, 25), date(2026, 10, 28))])


def test_negative_reverse_detected_before_loading_82_candles(tmp_path):
    db=PortfolioStore(tmp_path/'negative.db')
    populate(db,include_ops=False)
    # Bought two shares yesterday, only one held today, and no intervening sale.
    # This is inconsistent with a non-short account and MUST be reported.
    db.save_batch(date(2026,10,28),[],[
        ('a',op('inconsistent','2026-10-27','OPERATION_TYPE_BUY',-200,'S1',2))
    ],[])

    class MustNotFetch(Market):
        def daily_candles(self,*args,**kwargs):
            pytest.fail('Preflight should stop before market candle downloads')

    with pytest.raises(ValueError,match='2026-10-27.*отрицательный остаток.*d') as ex:
        HistoricalBackfill(MustNotFetch(),('a',),db).run(days=4)
    assert 'OPERATION_TYPE_BUY=2' in str(ex.value)
    assert db.get_dashboard_data()['history_estimated']==[]


def test_partial_backfill_keeps_only_provable_recent_suffix(tmp_path):
    db=PortfolioStore(tmp_path/'partial_quantity.db')
    populate(db,include_ops=False)
    db.save_batch(date(2026,10,28),[],[
        ('a',op('inconsistent','2026-10-26','OPERATION_TYPE_BUY',-200,'S1',2))
    ],[])
    result=HistoricalBackfill(Market(),('a',),db).run(days=4,partial=True)
    assert result['points']==2
    assert result['period'].startswith('2026-10-26')
    assert any('отрицательный остаток' in w for w in result['warnings'])
    assert [r['day'] for r in db.get_dashboard_data()['history_estimated']] == [
        '2026-10-26','2026-10-27'
    ]


def test_same_day_buy_sell_net_not_falsely_negative(tmp_path):
    db=PortfolioStore(tmp_path/'same_day.db')
    populate(db,include_ops=False)
    # Both operations occurred during the same day, but the reverse iteration
    # order is arbitrary. 2 bought and 2 sold cancel out at end of day.
    db.save_batch(date(2026,10,28),[],[
        ('a',op('buytwo','2026-10-27','OPERATION_TYPE_BUY',-200,'S1',2)),
        ('a',op('selltwo','2026-10-27','OPERATION_TYPE_SELL',200,'S1',2)),
    ],[])
    result=HistoricalBackfill(Market(),('a',),db).run(days=4)
    assert result['points']==2


def test_bond_full_repayment_without_quantity_is_cutoff(tmp_path):
    db=PortfolioStore(tmp_path/'redeemed.db')
    populate(db,include_ops=False)
    db.save_batch(date(2026,10,28),[],[
        ('a',op('repay','2026-10-26','OPERATION_TYPE_BOND_REPAYMENT_FULL',1000,'B1'))
    ],[])
    with pytest.raises(ValueError,match='полное погашение облигации'):
        HistoricalBackfill(Market(),('a',),db).run(days=4)
    result=HistoricalBackfill(Market(),('a',),db).run(days=4,partial=True)
    assert result['points']==2
    assert result['period'].startswith('2026-10-26')


def test_local_date_from_operations_used_for_reversal(tmp_path):
    db=PortfolioStore(tmp_path/'timezone.db')
    populate(db,include_ops=False)
    # 27 Oct 22:30 UTC is 28 Oct 01:30 in Moscow; must reverse on 28th.
    trade=op('trade-local-day','2026-10-27','OPERATION_TYPE_BUY',-50,'S1',1)
    trade['date']='2026-10-27T22:30:00Z'
    db.save_batch(date(2026,10,28),[],[('a',trade)],[])

    class ZonedMarket(Market):
        tz=ZoneInfo('Europe/Moscow')

    # Captured snapshot is generated now, after trade event in the test setup.
    report=HistoricalBackfill(ZonedMarket(),('a',),db).run(days=4)
    assert report['points']==3
    values={p['day']:p['total'] for p in db.get_dashboard_data()['history_estimated']}
    assert values['2026-10-27']==100.0
