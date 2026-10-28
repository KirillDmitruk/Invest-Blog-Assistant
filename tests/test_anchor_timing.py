"""Regression: a trade made AFTER a portfolio request must not be reversed.

GetPortfolio is a live snapshot, but GetOperationsByCursor is queried later;
saving the snapshot's time only at transaction commit creates false negative
quantities for same-day buys.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import invest_blog.sync as sync_module
from invest_blog.model import Instrument
from invest_blog.sync import PortfolioSync
from invest_blog.history import HistoricalBackfill
from invest_blog.portfolio_store import PortfolioStore


def money(value):
    return {'units': str(value), 'nano': 0, 'currency': 'RUB'}


class API:
    tz = ZoneInfo('UTC')

    def __init__(self, executed_at):
        self.executed_at = executed_at

    def portfolio(self, aid):
        return {
            'totalAmountPortfolio': money(150),
            'totalAmountShares': money(100),
            'totalAmountBonds': money(0),
            'totalAmountEtf': money(0),
            'totalAmountCurrencies': money(50),
            'positions': [{
                'instrumentUid': 'S1', 'figi': 'FIG1', 'instrumentType': 'share',
                'quantity': {'units': '1', 'nano': 0},
                'currentPrice': money(100),
            }],
        }

    def instrument(self, uid='', figi=''):
        return Instrument('S1', 'FIG1', 'Share', '', 'TEST', 'share', 'RUB', {})

    def operations(self, aid, start, end):
        return [{
            'id': 'buy-later', 'date': self.executed_at,
            'type': 'OPERATION_TYPE_BUY', 'state': 'OPERATION_STATE_EXECUTED',
            'payment': money(-200), 'instrumentUid': 'S1', 'quantityDone': '2',
        }]

    def daily_candles(self, uid, start, end):
        return [{
            'time': '2026-10-27T12:00:00Z', 'close': money(90),
            'isComplete': True, 'priceCurrency': 'RUB',
        }]


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 28, 11, 0, tzinfo=tz or timezone.utc)


def test_snapshot_timestamp_is_request_time_not_save_time(monkeypatch, tmp_path):
    monkeypatch.setattr(sync_module, 'datetime', Clock)
    store = PortfolioStore(tmp_path / 'p.db')
    api = API('2026-10-28T12:00:00Z')
    PortfolioSync(api, ('a',), store).run(date(2026, 10, 28), days=2)
    with store.connect() as db:
        snap = db.execute('SELECT captured_at FROM portfolio_snapshots WHERE account_id=?', ('a',)).fetchone()
    assert snap['captured_at'] == '2026-10-28T11:00:00+00:00'

    # A buy done AFTER the snapshot cannot be reversed from its holdings.
    result = HistoricalBackfill(api, ('a',), store).run(days=2)
    assert result['points'] == 1
    assert any('позже момента снимка' in w for w in result['warnings'])
    assert store.get_dashboard_data()['history_estimated'][0]['total'] == 140


def test_buy_before_snapshot_remains_a_real_discrepancy(monkeypatch, tmp_path):
    monkeypatch.setattr(sync_module, 'datetime', Clock)
    store = PortfolioStore(tmp_path / 'p.db')
    api = API('2026-10-28T10:00:00Z')
    PortfolioSync(api, ('a',), store).run(date(2026, 10, 28), days=2)
    with pytest.raises(ValueError, match='отрицательный остаток'):
        HistoricalBackfill(api, ('a',), store).run(days=2)
