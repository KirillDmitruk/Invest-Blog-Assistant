from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from invest_blog.dashboard import create_app
from invest_blog.model import Draft, Instrument
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.storage import DraftStore
from invest_blog.sync import PortfolioSync
from invest_blog.tinvest import TInvestError


def m(n, currency='RUB'):
    return {'units':str(n),'nano':0,'currency':currency}


class Api:
    tz=ZoneInfo('Europe/Moscow')
    broken=False
    def portfolio(self, aid):
        return {'totalAmountPortfolio':m(100000), 'totalAmountBonds':m(70000),
                'totalAmountShares':m(20000), 'totalAmountCurrencies':m(10000),
                'totalAmountEtf':m(0),'positions':[
            {'instrumentUid':'B1', 'instrumentType':'bond', 'quantity':m(5), 'currentPrice':m(10000)},
            {'instrumentUid':'S1', 'instrumentType':'share', 'quantity':m(10), 'currentPrice':m(2000)}]}

    def operations(self, aid, start, end):
        if self.broken:raise TInvestError('API недоступен')
        return [
            {'id':'x1','date':'2026-10-01T00:00:00Z','type':'OPERATION_TYPE_COUPON',
             'state':'OPERATION_STATE_EXECUTED','payment':m(500),'instrumentUid':'B1'},
            {'id':'x2','date':'2026-10-10T00:00:00Z','type':'OPERATION_TYPE_DIVIDEND',
             'state':'OPERATION_STATE_EXECUTED','payment':m(140),'instrumentUid':'S1'},
            {'id':'x3','date':'2026-10-11T00:00:00Z','type':'OPERATION_TYPE_INPUT',
             'state':'OPERATION_STATE_EXECUTED','payment':m(5000)},
        ]

    def instrument(self, uid='',figi=''):
        return Instrument(uid=uid,figi=figi,name='Название '+uid, isin='RU'+uid,
                          ticker=uid,kind='bond' if uid=='B1' else 'share', currency='RUB',raw={})

    def bond(self,uid):
        return {'maturityDate':'2028-04-01T00:00:00Z'}


def test_sync_idempotent_and_history(tmp_path):
    store=PortfolioStore(tmp_path/'state.sqlite3')
    sync=PortfolioSync(Api(),('a',),store)
    a=sync.run(date(2026,10,28),days=365)
    assert a['operations']==3 and a['positions']==2
    sync.run(date(2026,10,28),days=365)
    with store.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM broker_operations').fetchone()[0]==3
        assert conn.execute('SELECT COUNT(*) FROM portfolio_snapshots').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM position_snapshots').fetchone()[0]==2
    data=store.get_dashboard_data()
    assert data['has_data'] and data['total_rub']==100000
    assert next(x for x in data['income'] if x['month']=='2026-10')['coupons']=={'RUB':500.0}
    assert data['maturity'][0]['year']=='2028'
    assert any('Позиц' in a['text'] for a in data['alerts']) or data['alerts']
    assert data['history'][0]['day']=='2026-10-28'


def test_failed_sync_preserves_previous_snapshot(tmp_path):
    api=Api();db=PortfolioStore(tmp_path/'state.sqlite3');runner=PortfolioSync(api,('a',),db)
    runner.run(date(2026,10,28))
    api.broken=True
    with pytest.raises(TInvestError):runner.run(date(2026,10,29))
    assert db.latest_day()=='2026-10-28'


def test_multi_account_separate_operations(tmp_path):
    db=PortfolioStore(tmp_path/'state.sqlite3')
    PortfolioSync(Api(),('a','b'),db).run(date(2026,10,28))
    data=db.get_dashboard_data()
    assert data['total_rub']==200000
    assert len(data['positions'])==2
    assert next(x for x in data['income'] if x['month']=='2026-10')['coupons']['RUB']==1000.0


def test_legacy_draft_db_migration_and_states(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE drafts(id INTEGER PRIMARY KEY AUTOINCREMENT,dedup_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,period TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL,sent_at TEXT);
            CREATE TABLE telegram_messages(message_id INTEGER PRIMARY KEY,draft_id INTEGER NOT NULL);
            INSERT INTO drafts(dedup_key,kind,period,body,created_at) VALUES ('monthly:2026-09','monthly','2026-09','старый пост','2026-10-01');''')
    store=DraftStore(path)
    assert store.get(1)['body']=='старый пост'
    assert store.get(1)['scheduled_at'] is None
    assert store.change_state(1,'review')
    assert store.change_state(1,'approved')
    assert store.schedule(1,'2026-11-01T10:00:00+00:00')
    assert len(store.due_scheduled('2026-11-01T10:00:00+00:00'))==1
    assert store.claim_publish(1,scheduled=True)
    assert not store.claim_publish(1,scheduled=True)
    store.publish_success(1,123)
    assert store.get(1)['state']=='published'
    assert not store.change_state(1,'review')


def test_dashboard_local_read_only(tmp_path):
    path=tmp_path/'site.sqlite3'
    DraftStore(path)
    PortfolioStore(path)
    client=TestClient(create_app(path))
    assert client.get('/api/health').status_code==200
    assert not client.get('/api/overview').json()['has_data']
    assert client.get('/').status_code==200
    assert client.post('/api/overview').status_code==405
    assert 'TINVEST_TOKEN' not in client.get('/').text


def test_alerts_do_not_invent_nonrub_weights(tmp_path):
    db=PortfolioStore(tmp_path/'state.sqlite3')
    p={'totalAmountPortfolio':m(100000),'totalAmountBonds':m(80000),
       'totalAmountShares':m(20000),'totalAmountCurrencies':m(0),'totalAmountEtf':m(0)}
    data={'account_id':'a','portfolio':p,'positions':[{'id':'U','name':'USD bond','ticker':'U',
        'kind':'bond','quantity':'10','price':'50','currency':'USD','nkd':None,'maturity':None}]}
    db.save_batch(date(2026,10,28),[data],[],[])
    response=db.get_dashboard_data()
    assert response['positions'][0]['weight_pct'] is None
    assert any('рублёвой оценки' in a['text'] for a in response['alerts'])


def test_topic_memory(tmp_path):
    s=PortfolioStore(tmp_path/'x.sqlite3')
    s.record_topic('paper','B1',7)
    s.record_topic('paper','S1',8)
    assert s.recent_topics('paper')==['S1','B1']


def test_edit_requires_reapproval_and_unschedules(tmp_path):
    store=DraftStore(tmp_path/'db.sqlite3')
    did,_=store.insert(Draft('paper','2026-W44','Текст'),scheduled=False)
    assert store.change_state(did,'review')
    assert store.change_state(did,'approved')
    assert store.schedule(did,'2026-11-01T10:00:00+00:00')
    store.set_body(did,'Новый текст')
    assert store.get(did)['state']=='review'
    assert store.get(did)['scheduled_at'] is None
    assert not store.claim_publish(did)
    assert store.change_state(did,'approved')
    assert store.claim_publish(did)
    assert not store.claim_publish(did)


def test_sync_refuses_missing_payment(tmp_path):
    class Broken(Api):
        def operations(self, aid, start, end):
            rows=super().operations(aid,start,end)
            rows[0].pop('payment')
            return rows
    db=PortfolioStore(tmp_path/'db.sqlite3')
    with pytest.raises(TInvestError):
        PortfolioSync(Broken(),('a',),db).run(date(2026,10,28))
    assert db.latest_day() is None


def test_paper_history_selection(tmp_path):
    from invest_blog.posts import PostGenerator
    class HoldingApi(Api):
        def operations(self, aid, start, end):return []
    gen=PostGenerator(HoldingApi(),('a',),tmp_path/'events.json',recent_papers=('B1',))
    generated=gen.paper(date(2026,10,28))
    assert gen.selected_paper_uid=='S1'
    assert 'Название S1' in generated.body


def test_integrity_flags_potential_duplicate_not_deletes(tmp_path):
    from invest_blog.integrity import check_integrity
    db=PortfolioStore(tmp_path/'db.sqlite3')
    p={'totalAmountPortfolio':m(100),'totalAmountBonds':m(0),'totalAmountShares':m(0),
       'totalAmountCurrencies':m(100),'totalAmountEtf':m(0)}
    a={'id':'1','date':'2026-10-26T07:00:00+03:00','type':'OPERATION_TYPE_COUPON',
       'payment':m(10),'state':'OPERATION_STATE_EXECUTED'}
    b={**a,'id':'2'}
    db.save_batch(date(2026,10,28),[{'account_id':'a','portfolio':p,'positions':[]}], [('a',a),('a',b)], [])
    audit=check_integrity(db)
    assert audit['operations']==2
    assert len(audit['possible_duplicates'])==1
