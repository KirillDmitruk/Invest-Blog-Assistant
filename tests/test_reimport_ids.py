"""T-Invest operation IDs may change between syncs. Never double count them.

These tests exercise the same SQLite store and sync semantics as production.
"""
from datetime import date
import json

import pytest

from invest_blog.portfolio_store import PortfolioStore
from invest_blog.tinvest import TInvestError


def money(v):
    return {"units": str(v), "nano": 0, "currency": "RUB"}


def sample(id, day='2026-10-28', quantity=1):
    return {'id': id, 'date': day+'T13:06:31+03:00',
            'type': 'OPERATION_TYPE_BUY', 'state': 'OPERATION_STATE_EXECUTED',
            'instrumentUid': 'tatneft-share-uid', 'quantityDone': str(quantity),
            'payment': money(-100)}


def insert_snapshot(store):
    store.save_batch(date(2026,10,28), [dict(account_id='a', portfolio={
        'totalAmountPortfolio': money(100),
        'totalAmountCurrencies': money(0),
        'totalAmountBonds': money(0),
        'totalAmountShares': money(100),
        'totalAmountEtf': money(0),
        }, positions=[])], [], [])


def test_changing_broker_operation_id_does_not_duplicate(tmp_path):
    store = PortfolioStore(tmp_path/'p.sqlite3')
    insert_snapshot(store)
    dates = [('a', date(2026,10,27), date(2026,10,28))]
    store.save_batch(date(2026,10,28), [], [('a', sample('old-id'))], [], coverage_ranges=dates)
    store.save_batch(date(2026,10,28), [], [('a', sample('new-id'))], [], coverage_ranges=dates)
    with store.connect() as db:
        rows = db.execute('SELECT raw_json FROM broker_operations').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]['raw_json'])['id'] == 'new-id'


def test_only_refreshed_account_and_dates_replaced(tmp_path):
    store = PortfolioStore(tmp_path/'p.sqlite3')
    insert_snapshot(store)
    store.save_batch(date(2026,10,28), [], [
        ('a',sample('a-old', '2026-10-28')),
        ('a',sample('a-preserved','2026-10-25')),
        ('b',sample('b-preserved','2026-10-28')),
    ], [])
    store.save_batch(date(2026,10,28), [], [
        ('a', sample('a-new', '2026-10-28'))
    ], [], coverage_ranges=[('a',date(2026,10,28),date(2026,10,28))])
    with store.connect() as db:
        ids = {json.loads(row['raw_json'])['id'] for row in db.execute('SELECT raw_json FROM broker_operations')}
    assert ids == {'a-new','a-preserved','b-preserved'}


def test_bad_import_rolls_back_delete(tmp_path):
    store = PortfolioStore(tmp_path/'p.sqlite3')
    insert_snapshot(store)
    store.save_batch(date(2026,10,28), [], [('a',sample('old'))], [])
    broken = sample('missing-identifiers')
    broken.pop('id')
    with pytest.raises(ValueError, match='cursor/id'):
        store.save_batch(date(2026,10,28), [], [('a',broken)], [],
                         coverage_ranges=[('a',date(2026,10,28),date(2026,10,28))])
    with store.connect() as db:
        rows=db.execute('SELECT raw_json FROM broker_operations').fetchall()
    assert len(rows)==1
    assert json.loads(rows[0]['raw_json'])['id']=='old'


def test_empty_fresh_history_removes_stale_old_ids(tmp_path):
    store=PortfolioStore(tmp_path/'p.sqlite3')
    insert_snapshot(store)
    store.save_batch(date(2026,10,28), [], [('a',sample('old'))], [])
    store.save_batch(date(2026,10,28), [], [], [],
                     coverage_ranges=[('a',date(2026,10,28),date(2026,10,28))])
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM broker_operations').fetchone()[0] == 0


def test_real_sync_replaces_id_that_changed_between_api_calls(tmp_path):
    from zoneinfo import ZoneInfo
    from invest_blog.sync import PortfolioSync

    class API:
        tz = ZoneInfo('Europe/Moscow')
        item_id = 'old-id'
        failed = False

        def portfolio(self, aid):
            return {'positions': [], 'totalAmountPortfolio': money(100),
                    'totalAmountShares': money(0), 'totalAmountBonds': money(0),
                    'totalAmountEtf': money(0), 'totalAmountCurrencies': money(100)}

        def operations(self, aid, start, end):
            if self.failed:
                raise TInvestError('Пагинация прервалась')
            return [sample(self.item_id)]

    api = API()
    store = PortfolioStore(tmp_path/'sync.sqlite3')
    sync = PortfolioSync(api, ('a',), store)
    sync.run(date(2026,10,28), days=365)
    api.item_id = 'new-id'
    sync.run(date(2026,10,28), days=365)
    with store.connect() as db:
        rows = db.execute('SELECT raw_json FROM broker_operations').fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]['raw_json'])['id'] == 'new-id'

    api.failed = True
    with pytest.raises(TInvestError):
        sync.run(date(2026,10,28), days=365)
    with store.connect() as db:
        ids = [json.loads(r['raw_json'])['id'] for r in db.execute('SELECT raw_json FROM broker_operations')]
    assert ids == ['new-id']
