"""Manual holdings must not fabricate values or alter brokerage history."""
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from invest_blog.dashboard import create_app
from invest_blog.walt_portfolio import WaltPortfolio, now


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('TINVEST_ACCOUNT_IDS', '')
    path = tmp_path / 'app.db'
    with TestClient(create_app(path)) as client:
        headers = {'X-CSRF-Token': client.get('/api/editor-token').json()['token']}
        yield client, headers, WaltPortfolio(path)


def position(**fields):
    return dict(symbol='BTC', name='Bitcoin', kind='crypto', quantity='0.1',
                unit_price_usd='60000', valued_on=now().date().isoformat(), **fields)


def add(client, headers, **overrides):
    payload = position()
    payload.update(overrides)
    response = client.post('/api/assets/walt', headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def seed_broker(store, account='a', day='2026-11-10', total='10000'):
    with store.connect() as db:
        db.execute('''INSERT INTO portfolio_snapshots
            (account_id,day,total_rub,bonds_rub,shares_rub,etf_rub,cash_rub,other_rub,captured_at)
            VALUES(?,?,?,?,'0','0','0','0',?)''', (account, day, total, total, day+'T12:00:00+00:00'))


def set_fx(client, headers, rate='100', revision=0):
    return client.put('/api/assets/fx', headers=headers,
                      json={'rate': rate, 'as_of': now().date().isoformat(), 'revision': revision})


def test_empty_and_missing_values_do_not_look_like_complete_zero(context):
    client, headers, store = context
    result = client.get('/api/assets/overview').json()
    assert result['combined']['total_rub'] is None
    assert result['walt']['total_usd'] is None
    assert result['broker']['total_rub'] is None
    assert client.get('/api/assets/history').json() == []
    add(client, headers, unit_price_usd=None)
    result = client.get('/api/assets/overview').json()
    assert result['walt']['missing_prices'] == ['BTC']
    assert result['walt']['total_usd'] is None
    assert result['combined']['complete'] is False
    assert result['return_pct'] is None


def test_combines_latest_snapshot_of_each_account_without_double_counting(context):
    client, headers, store = context
    seed_broker(store, 'a', '2026-11-10', '10000')
    seed_broker(store, 'a', '2026-11-11', '12000')
    seed_broker(store, 'b', '2026-11-10', '3000')
    add(client, headers)
    assert set_fx(client, headers).status_code == 200
    result = client.get('/api/assets/overview').json()
    assert result['broker']['total_rub'] == '15000.00'
    assert result['walt']['total_usd'] == '6000.00'
    assert result['combined']['total_rub'] == '615000.00'
    assert result['combined']['total_usd'] == '6150.00'
    assert len(result['broker']['accounts']) == 2
    assert any('разные даты' in text for text in result['warnings'])
    assert sum(Decimal(x['value_rub']) for x in result['allocation']) == Decimal('615000')
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM portfolio_snapshots').fetchone()[0] == 3


def test_configured_accounts_exclude_old_accounts_and_report_missing(context, monkeypatch):
    client, headers, store = context
    seed_broker(store, 'a')
    seed_broker(store, 'old', total='100000000')
    monkeypatch.setenv('TINVEST_ACCOUNT_IDS', 'a,missing')
    result = client.get('/api/assets/overview').json()
    assert result['broker']['known_total_rub'] == '10000.00'
    assert result['broker']['missing_accounts'] == ['missing']
    assert result['broker']['total_rub'] is None


def test_zero_price_is_explicit_and_usdt_is_not_assumed_equal_to_usd(context):
    client, headers, store = context
    add(client, headers, symbol='USDT', kind='stablecoin', quantity='100', unit_price_usd='0.98')
    add(client, headers, symbol='OLD', quantity='1', unit_price_usd='0')
    result = client.get('/api/assets/overview').json()
    assert result['walt']['total_usd'] == '98.00'
    assert result['walt']['missing_prices'] == []
    assert result['combined']['total_rub'] is None


def test_token_ticker_is_preserved_and_duplicate_is_rejected(context):
    client, headers, store = context
    record = add(client, headers, symbol='SPYx', kind='tokenized_etf')
    assert record['symbol'] == 'SPYx'
    assert client.post('/api/assets/walt', headers=headers,
                       json={**position(), 'symbol':'spyX'}).status_code == 409
    add(client, headers, symbol='SPY', kind='other')
    assert len(client.get('/api/assets/overview').json()['positions']) == 2


def test_revisions_and_immutable_history_survive_update_delete_recreate(context):
    client, headers, store = context
    record = add(client, headers)
    endpoint = '/api/assets/walt/'+str(record['id'])
    fields = {**position(), 'quantity':'0.2', 'revision':record['revision']}
    assert client.put(endpoint, headers=headers, json=fields).status_code == 200
    assert client.put(endpoint, headers=headers, json=fields).status_code == 409
    assert client.request('DELETE', endpoint, headers=headers, json={'revision':1}).status_code == 409
    history = client.get('/api/assets/history').json()
    assert len(history) == 2
    assert history[-1]['walt']['total_usd'] == '6000.00'
    assert history[0]['walt']['total_usd'] == '12000.00'
    assert client.request('DELETE', endpoint, headers=headers, json={'revision':2}).status_code == 204
    assert client.get('/api/assets/overview').json()['positions'] == []
    replacement = add(client, headers)
    assert replacement['id'] != record['id']
    assert len(client.get('/api/assets/history').json()) == 4


@pytest.mark.parametrize('overrides', [
    {'quantity':'0'}, {'quantity':'-1'}, {'quantity':'NaN'}, {'quantity':'Infinity'},
    {'quantity':'1000000000001'}, {'quantity':'0.0000000000000000001'},
    {'unit_price_usd':'-1'}, {'unit_price_usd':'NaN'}, {'cost_usd':'-1'},
    {'kind':'stock'}, {'symbol':'<script>'}, {'symbol':' '}, {'name':' '},
])
def test_invalid_positions_are_rejected_without_history(context, overrides):
    client, headers, store = context
    assert client.post('/api/assets/walt', headers=headers, json={**position(), **overrides}).status_code == 422
    assert store.history() == []


def test_future_date_is_rejected(context):
    client, headers, store = context
    future = (now().date()+timedelta(days=1)).isoformat()
    assert client.post('/api/assets/walt', headers=headers, json={**position(),'valued_on':future}).status_code == 422
    assert client.put('/api/assets/fx', headers=headers, json={'rate':'100','as_of':future,'revision':0}).status_code == 422


def test_decimal_precision_and_fx_revision(context):
    client, headers, store = context
    add(client, headers, quantity='0.123456789123456789', unit_price_usd='123.456789123456789')
    assert set_fx(client, headers, '90.1234567890').status_code == 200
    assert set_fx(client, headers, '100', revision=0).status_code == 409
    result = client.get('/api/assets/overview').json()
    assert result['positions'][0]['quantity'] == '0.123456789123456789'
    assert result['fx']['usd_rub'] == '90.1234567890'
    assert result['walt']['total_usd'] == '15.24'


def test_writes_require_csrf_and_same_origin(context):
    client, headers, store = context
    for path, method, payload in [('/walt','POST',position()),('/fx','PUT',{'rate':'100','as_of':now().date().isoformat(),'revision':0}),
                                   ('/fx/cbr','POST',{'revision':0}),('/snapshot','POST',{})]:
        assert client.request(method, '/api/assets'+path, json=payload).status_code == 403
        assert client.request(method, '/api/assets'+path, headers={**headers,'Origin':'https://evil.example'},json=payload).status_code == 403
    assert store.history() == []


def test_cbr_failure_keeps_previous_rate_and_reading_never_refreshes_it(context, monkeypatch):
    client, headers, store = context
    assert set_fx(client, headers).status_code == 200
    calls = []
    def unavailable():
        calls.append(1)
        raise ConnectionError('offline')
    monkeypatch.setattr('invest_blog.walt_routes.fetch_cbr_usd', unavailable)
    assert client.get('/api/assets/overview').status_code == 200
    assert calls == []
    assert client.post('/api/assets/fx/cbr', headers=headers, json={'revision':1}).status_code == 503
    assert calls == [1]
    assert store.summary()['fx']['usd_rub'] == '100'
    assert len(store.history()) == 1


def test_cbr_explicit_update_and_manual_snapshot(context, monkeypatch):
    client, headers, store = context
    monkeypatch.setattr('invest_blog.walt_routes.fetch_cbr_usd',
                        lambda: {'rate':'95.5','date':now().date().isoformat()})
    assert client.post('/api/assets/fx/cbr', headers=headers, json={'revision':0}).status_code == 200
    assert store.summary()['fx']['source'] == 'cbr'
    assert client.post('/api/assets/snapshot', headers=headers, json={}).status_code == 201
    assert len(store.history()) == 2
    assert client.get('/static/assets.js').status_code == 200
