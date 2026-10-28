import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from invest_blog.goals import GoalStore, current_measures, evaluate_goals
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.risk import analyze_portfolio
from invest_blog.dashboard import create_app


@pytest.fixture
def store(tmp_path):
    db = PortfolioStore(tmp_path / 'data.sqlite3')
    with db.connect() as cx:
        cx.execute('''INSERT INTO portfolio_snapshots VALUES
            ('2026-10-28','acc', '100000','70000','10000','15000','5000','0','2026-10-28T08:00:00Z')''')
        for uid, name, quantity, maturity, issuer in [
            ('one','Облигация А','10','2028-06-01','issuer_a'),
            ('two','Облигация Б','20','2029-06-01','issuer_b')
        ]:
            cx.execute('''INSERT INTO position_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                       ('2026-10-28','acc',uid,name,name,'bond',quantity,'100','PT.', '0',maturity))
            cx.execute('INSERT INTO bond_metadata VALUES (?,?,?)', (uid, json.dumps({
                'nominal': {'units':'1000','nano':0,'currency':'rub'}, 'issuerUid':issuer,
                'maturityDate':maturity
            }), '2026-10-28'))
        cx.execute('INSERT INTO operation_import_ranges VALUES (?,?,?)', ('acc','2026-07-01','2026-10-28'))
        for dt,typ,amt,cur in [('2026-07-10','OPERATION_TYPE_COUPON','600','RUB'),
                                ('2026-08-10','OPERATION_TYPE_COUPON','300','RUB'),
                                ('2026-09-10','OPERATION_TYPE_DIVIDEND','900','RUB'),
                                ('2026-09-12','OPERATION_TYPE_COUPON','100','USD')]:
            cx.execute('INSERT INTO broker_operations VALUES (?,?,?,?,?,?,?,?,?)',
                       ('acc',dt+typ,dt+'T10:00:00+03:00',typ,amt,cur,'one','{}','2026-10-28T00:00:00Z'))
    return db


def test_score_with_valid_issuer_and_maturity_data(store):
    result = analyze_portfolio(store, today=date(2026,10,28))
    assert result['has_data']
    assert result['bond_share_pct'] == 70
    assert result['risk_metrics']['principal_known_rub'] == 30000
    assert result['risk_metrics']['principal_coverage_issues']['complete']
    assert result['health']['score'] is not None
    assert result['health']['evaluated_weight_pct'] == 100
    assert any(f['key']=='issuer' for f in result['findings'])


def test_missing_issuer_does_not_mean_safe(store):
    with store.connect() as cx:
        cx.execute("UPDATE bond_metadata SET raw_json=? WHERE instrument_id='two'", (
            json.dumps({'nominal':{'units':'1000','nano':0,'currency':'rub'}}),))
    result = analyze_portfolio(store, today=date(2026,10,28))
    issuer = next(c for c in result['health']['checks'] if c['key']=='issuer')
    assert issuer['score'] is None
    assert result['risk_metrics']['largest_issuer_pct_of_bond_principal'] is None
    assert any('issuerUid' in note for note in result['data_quality'])


def test_unknown_nominal_not_inferred_from_quantity(store):
    with store.connect() as cx:
        cx.execute("DELETE FROM bond_metadata WHERE instrument_id='two'")
    result = analyze_portfolio(store, today=date(2026,10,28))
    assert result['risk_metrics']['principal_known_rub'] == 10000
    assert result['health']['score'] is None
    assert next(c for c in result['health']['checks'] if c['key']=='maturity')['status']=='unknown'


def test_risk_no_snapshots(tmp_path):
    db = PortfolioStore(tmp_path/'empty.db')
    result = analyze_portfolio(db)
    assert result['has_data'] is False


def test_stale_snapshot_not_good(store):
    risk = analyze_portfolio(store, today=date(2026,11,15))
    assert next(c for c in risk['health']['checks'] if c['key']=='freshness')['score'] is None
    assert any('Снимку портфеля' in q for q in risk['data_quality'])
    assert risk['health']['score'] is None


def test_monthly_income_complete_months_only(store):
    values = current_measures(store)
    assert values['verified_months']==3
    assert values['monthly_coupons']==300
    assert values['monthly_income']==600
    assert values['bond_share']==70
    assert values['portfolio_value']==100000


def test_goal_store_validation_and_crud(store):
    goals = GoalStore(store)
    with pytest.raises(ValueError):
        goals.upsert({'name':'not a number','kind':'portfolio_value','target':'NaN'})
    with pytest.raises(ValueError):
        goals.upsert({'name':'wrong ratio','kind':'bond_share','target':'101'})
    row = goals.upsert({'name':'Пассивный доход','kind':'monthly_income','target':'1200','due_date':'2028-01-01'})
    data = evaluate_goals(goals)['goals'][0]
    assert data['progress_pct'] == 50
    assert data['status']=='in_progress'
    changed = goals.upsert({'name':'Купоны','kind':'monthly_coupons','target':'500'},row['id'])
    assert changed['name']=='Купоны'
    goals.delete(row['id'])
    assert goals.list()==[]
    with pytest.raises(KeyError):
        goals.delete(row['id'])


def test_income_unverified_is_unknown(store):
    with store.connect() as cx:
        cx.execute('DELETE FROM operation_import_ranges')
    goalstore=GoalStore(store)
    goalstore.upsert({'name':'Доход','kind':'monthly_income','target':'1000'})
    g=evaluate_goals(goalstore)['goals'][0]
    assert g['current'] is None
    assert g['status']=='unknown'


def test_local_api_goal_editor_is_csrf_protected(store):
    client = TestClient(create_app(store.path))
    assert client.get('/api/risk').status_code == 200
    assert client.get('/api/goals').status_code == 200
    payload={'name':'Капитал','kind':'portfolio_value','target':'500000'}
    assert client.post('/api/goals',json=payload).status_code==403
    nonce = client.get('/api/editor-token').json()['token']
    headers={'X-CSRF-Token':nonce}
    response=client.post('/api/goals',json=payload,headers=headers)
    assert response.status_code==201, response.text
    gid=response.json()['id']
    assert client.put(f'/api/goals/{gid}',json={**payload,'target':'600000'},headers=headers).status_code==200
    assert client.delete(f'/api/goals/{gid}',headers=headers).status_code==204
    assert client.get('/api/goals').json()['goals']==[]
    assert client.post('/api/goals',json=payload,headers={**headers,'Origin':'https://evil.example'}).status_code==403
