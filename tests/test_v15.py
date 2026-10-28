"""v1.5: no fabricated returns, defensive cashflow accounting, CSV exports."""
from datetime import date, timedelta
from decimal import Decimal
import io
import csv
import zipfile

import pytest
from fastapi.testclient import TestClient

from invest_blog.portfolio_store import PortfolioStore
from invest_blog.performance import evaluate, load_history, summarize, _xirr
from invest_blog.exporter import csv_bytes, export_zip, export_tables
from invest_blog.dashboard import create_app

D0 = date(2025,1,1)
D1 = date(2026,1,1)


def setup_store(tmp_path, *, values=(100, 110), flow=None, days=(D0,D1), coverage=True, accounts=('a',)):
    store = PortfolioStore(tmp_path / 'portfolio.sqlite')
    with store.connect() as db:
        for acc in accounts:
            for d,v in zip(days, values):
                db.execute('''INSERT INTO portfolio_snapshots
                  (day,account_id,total_rub,bonds_rub,shares_rub,etf_rub,cash_rub,other_rub,captured_at)
                  VALUES (?,?,?,?,?,?,?,?,?)''',
                  (d.isoformat(),acc,str(v),'0','0','0',str(v),'0',d.isoformat()+'T15:00:00+00:00'))
            if coverage:
                db.execute('INSERT INTO operation_import_ranges VALUES (?,?,?)',
                           (acc, days[0].isoformat(), days[-1].isoformat()))
        if flow:
            for i,item in enumerate(flow):
                day,kind,value,cur = item
                db.execute('''INSERT INTO broker_operations
                 (account_id,op_key,executed_at,kind,amount,currency,instrument_id,raw_json,updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)''',('a','k'+str(i),day.isoformat()+'T12:00:00+03:00',
                                               kind,str(value),cur,None,'{}','2026-01-01'))
    return store


def test_no_cashflows_returns_exact_twr_and_xirr(tmp_path):
    st=setup_store(tmp_path)
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='ok'
    assert out['exact_twr'] is True
    assert out['twr_pct']==10
    assert abs(out['xirr_pct']-10.0072)<.01
    assert out['profit_rub']==10


def test_deposit_is_not_profit_and_twr_is_approximate(tmp_path):
    d=date(2025,7,1)
    st=setup_store(tmp_path,values=(100,165),flow=[(d,'OPERATION_TYPE_INPUT',50,'RUB')])
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='ok'
    assert out['profit_rub']==15
    assert out['net_contributions_rub']==50
    assert out['exact_twr'] is False
    assert out['xirr_pct']>0
    assert any('Modified Dietz' in w for w in out['warnings'])


def test_withdrawal_negative_net_contribution(tmp_path):
    st=setup_store(tmp_path,values=(100,90),flow=[(date(2025,7,1),'OPERATION_TYPE_OUTPUT',-20,'RUB')])
    out=evaluate(load_history(st),D0,D1)
    assert out['profit_rub']==10
    assert out['net_contributions_rub']==-20


def test_coupons_not_external_flows(tmp_path):
    st=setup_store(tmp_path,values=(100,110),flow=[(date(2025,7,1),'OPERATION_TYPE_COUPON',5,'RUB')])
    out=evaluate(load_history(st),D0,D1)
    assert out['net_contributions_rub']==0
    assert out['exact_twr'] is True


def test_no_operation_coverage_does_not_show_returns(tmp_path):
    st=setup_store(tmp_path,coverage=False)
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='unavailable'
    assert out['xirr_pct'] is None and out['twr_pct'] is None


def test_foreign_currency_external_flow_blocks_returns(tmp_path):
    st=setup_store(tmp_path,flow=[(date(2025,7,1),'OPERATION_TYPE_INPUT',100,'USD')])
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='unavailable'
    assert 'рублях' in out['reason']


def test_securities_transfer_without_cost_blocks_returns(tmp_path):
    st=setup_store(tmp_path,flow=[(date(2025,7,1),'OPERATION_TYPE_INPUT_SECURITIES',0,'RUB')])
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='unavailable'
    assert 'Перевод ценных бумаг' in out['reason']


def test_incomplete_account_snapshot_blocks_return(tmp_path):
    st=setup_store(tmp_path, accounts=('a','b'))
    with st.connect() as db:
        db.execute('DELETE FROM portfolio_snapshots WHERE day=? AND account_id=?',(D1.isoformat(),'b'))
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='unavailable'


def test_root_absent_when_no_sign_change():
    assert _xirr([(D0,Decimal(-100)),(D1,Decimal(-2))]) is None


def test_root_zero_when_no_gain():
    assert abs(_xirr([(D0,Decimal(-100)),(D1,Decimal(100))]))<1e-6


def test_exact_date_comparisons_require_snapshot(tmp_path):
    st=setup_store(tmp_path)
    out=summarize(st)
    one_month=out['comparisons'][0]
    assert one_month['status']=='unavailable'
    assert all(row['source']=='broker_snapshot' for row in out['history'])


def test_csv_escapes_formula_and_excel_utf8():
    raw=csv_bytes(['Value'],[['=1+1'],['-2'],['safe']])
    rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
    assert rows[1][0]=="'=1+1"
    assert rows[2][0]=="'-2"
    assert rows[3][0]=='safe'


def test_export_bundle_no_secrets(tmp_path):
    st=setup_store(tmp_path)
    report=summarize(st)
    data=export_zip(report)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert {'performance.csv','broker_snapshots.csv','external_cashflows.csv','methodology.txt','export_info.json'} <= set(z.namelist())
        assert b'account_id' not in z.read('broker_snapshots.csv')
        assert 'TWR' in z.read('performance.csv').decode('utf-8-sig')


def test_dashboard_api_csv_zip(tmp_path):
    st=setup_store(tmp_path)
    client=TestClient(create_app(st.path))
    response=client.get('/api/performance')
    assert response.status_code==200
    assert response.json()['period']['status']=='ok'
    z=client.get('/api/performance/export')
    assert z.status_code==200 and z.headers['content-type']=='application/zip'
    assert 'attachment' in z.headers['content-disposition']
    csv_response=client.get('/api/performance/export?table=performance')
    assert csv_response.status_code==200 and csv_response.headers['content-type'].startswith('text/csv')
    assert client.get('/api/performance/export?table=secret').status_code==422
    assert client.get('/api/performance?start=not-a-date').status_code==422


def test_never_uses_reconstructed_history(tmp_path):
    st=setup_store(tmp_path,coverage=False)
    with st.connect() as db:
        db.execute('''INSERT INTO reconstructed_portfolio_history
                    (day,account_id,total_rub,source,calculated_at) VALUES (?,?,?,?,?)''',
                    ('2025-07-01','a','1000','estimated','2026-01-01'))
    out=summarize(st)
    assert out['period']['status']=='unavailable'
    assert len(out['history'])==2


def test_external_flow_after_end_snapshot_excluded(tmp_path):
    st=setup_store(tmp_path, values=(100,110), flow=[(D1,'OPERATION_TYPE_INPUT',50,'RUB')])
    with st.connect() as db:
        db.execute('UPDATE broker_operations SET executed_at=?',(D1.isoformat()+'T23:55:00+00:00',))
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='ok'
    assert out['profit_rub']==10
    assert out['net_contributions_rub']==0


def test_external_flow_after_start_snapshot_same_day_is_counted(tmp_path):
    st=setup_store(tmp_path, values=(100,165), flow=[(D0,'OPERATION_TYPE_INPUT',50,'RUB')])
    with st.connect() as db:
        db.execute('UPDATE broker_operations SET executed_at=?',(D0.isoformat()+'T17:00:00+00:00',))
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='ok'
    assert out['profit_rub']==15
    assert out['net_contributions_rub']==50
    assert out['exact_twr'] is False


def test_no_exact_capture_timestamp_blocks_flow_return(tmp_path):
    st=setup_store(tmp_path,values=(100,165),flow=[(date(2025,7,1),'OPERATION_TYPE_INPUT',50,'RUB')])
    with st.connect() as db:
        db.execute('UPDATE portfolio_snapshots SET captured_at=? WHERE day=?',('2025-01-01',D0.isoformat()))
    out=evaluate(load_history(st),D0,D1)
    assert out['status']=='unavailable'
    assert 'времени снимков' in out['reason']


def test_export_extended_analytics_csv(tmp_path):
    from invest_blog.exporter import export_all_tables
    st=setup_store(tmp_path)
    tables=export_all_tables(st,summarize(st))
    assert 'current_positions.csv' in tables
    assert 'income_by_month.csv' in tables
    data=export_zip(summarize(st),st)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert 'income_by_month.csv' in z.namelist()
        assert 'current_positions.csv' in z.namelist()
