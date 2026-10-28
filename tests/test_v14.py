"""v1.4 editorial workflow tests. No real Telegram/API calls or personal data."""
from datetime import date
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from invest_blog.editorial import ContentPlanner, period_for
from invest_blog.dashboard import create_app
from invest_blog.model import Draft
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.posts import PostGenerator
from invest_blog.storage import DraftStore


@pytest.fixture
def data(tmp_path):
    path=tmp_path/'blog.sqlite3'
    portfolio=PortfolioStore(path)
    drafts=DraftStore(path)
    with portfolio.connect() as db:
        db.execute('''INSERT INTO portfolio_snapshots VALUES
            ('2026-10-28','acc','20000','10000','10000','0','0','0','2026-10-28T09:00:00Z')''')
        for uid,name,kind in [('bondA','Облигация А','bond'),('bondB','Облигация Б','bond'),('shareC','Акция C','share')]:
            db.execute('INSERT INTO position_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                       ('2026-10-28','acc',uid,name,uid,kind,'3','100','RUB',None,None))
        db.execute('INSERT INTO broker_operations VALUES (?,?,?,?,?,?,?,?,?)',
                   ('acc','buy-b','2026-10-23T10:00:00Z','OPERATION_TYPE_BUY','-300','RUB','bondB','{}','2026-10-28'))
    portfolio.record_topic('paper','bondA',1)
    portfolio.record_topic('glossary','Флоатер',2)
    return path,portfolio,drafts


def test_content_plan_order_and_antirepeats(data):
    _,portfolio,drafts=data
    p=ContentPlanner(portfolio,drafts)
    papers=p.paper_candidates()
    assert papers[0]['instrument_id']=='bondB'
    assert papers[0]['recent_buy']
    assert papers[-1]['instrument_id']=='bondA'
    titles=[x['title'] for x in p.glossary_suggestions()]
    assert 'Флоатер' not in titles[:3]
    cal=p.calendar(date(2026,11,1),7)
    assert [r['kind'] for r in cal]==['monthly','plan','paper','glossary','weekly']
    assert cal[0]['period']=='2026-10'
    assert period_for('weekly',date(2026,10,31))=='2026-10-26'


def test_calendar_indicates_existing_drafts(data):
    _,portfolio,drafts=data
    did,_=drafts.insert(Draft('plan','2026-11-02','План'),scheduled=False)
    dates=ContentPlanner(portfolio,drafts).calendar(date(2026,11,1),7)
    plan=next(x for x in dates if x['kind']=='plan')
    assert plan['draft_id']==did and plan['state']=='new'


def test_legacy_database_migrates_revision(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE drafts(id INTEGER PRIMARY KEY, dedup_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
          period TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,sent_at TEXT);
          INSERT INTO drafts VALUES(1,'legacy','paper','2026-10-26','Текст','review','2026-10-28',NULL);''')
    d=DraftStore(path)
    assert d.get(1)['revision']==1
    assert d.change_state(1,'approved',expected_revision=1)
    assert d.get(1)['revision']==2


def test_editor_conflict_and_reapproval(data):
    _,_,drafts=data
    did=drafts.create_manual('paper','2026-10-26','Первый текст')
    assert drafts.change_state(did,'approved')
    rev=drafts.get(did)['revision']
    assert drafts.queue_publish(did)
    assert len(drafts.pending_publications())==1
    drafts.update_body(did,'Исправлен',rev)
    assert drafts.get(did)['state']=='review'
    assert drafts.pending_publications()==[]
    with pytest.raises(RuntimeError):
        drafts.update_body(did,'Старая правка',rev)
    assert drafts.get(did)['body']=='Исправлен'
    assert not drafts.claim_publish(did)


def test_publish_requests_idempotence_and_single_winner(data):
    _,_,drafts=data
    did=drafts.create_manual('paper','today','В канал')
    assert not drafts.queue_publish(did)
    assert drafts.change_state(did,'approved')
    assert drafts.queue_publish(did) and drafts.queue_publish(did)
    assert len(drafts.pending_publications())==1
    assert drafts.claim_publish(did)
    assert drafts.pending_publications()==[]
    assert not drafts.claim_publish(did)
    drafts.publish_success(did,777)
    assert drafts.get(did)['state']=='published'
    with pytest.raises((RuntimeError,ValueError)):
        drafts.update_body(did,'Нельзя',drafts.get(did)['revision'])


def test_local_editor_api_auth_and_statuses(data):
    path,_,_=data
    client=TestClient(create_app(path))
    body={'kind':'paper','period':'2026-10-26','body':'Проверить бумагу'}
    assert client.post('/api/editor/drafts',json=body).status_code==403
    nonce=client.get('/api/editor-token').json()['token']
    headers={'X-CSRF-Token':nonce}
    assert client.post('/api/editor/drafts',json=body,headers={**headers,'Origin':'https://evil.example'}).status_code==403
    created=client.post('/api/editor/drafts',json=body,headers=headers)
    assert created.status_code==201,created.text
    did=created.json()['id'];rev=created.json()['revision']
    assert client.get('/api/editor/drafts/'+str(did)).json()['body']=='Проверить бумагу'
    assert client.put(f'/api/editor/drafts/{did}',json={'body':'Старое','revision':rev+100},headers=headers).status_code==409
    ok=client.put(f'/api/editor/drafts/{did}',json={'body':'Новая версия','revision':rev},headers=headers)
    assert ok.status_code==200 and ok.json()['state']=='review'
    wrong=client.post(f'/api/editor/drafts/{did}/action',json={'action':'approved','revision':rev},headers=headers)
    assert wrong.status_code==409
    rev=ok.json()['revision']
    good=client.post(f'/api/editor/drafts/{did}/action',json={'action':'approved','revision':rev},headers=headers)
    assert good.status_code==200
    rev=good.json()['revision']
    queued=client.post(f'/api/editor/drafts/{did}/action',json={'action':'queue_publish','revision':rev},headers=headers)
    assert queued.status_code==200
    assert next(r for r in client.get('/api/drafts').json() if r['id']==did)['queued']
    assert client.get('/api/content-plan').status_code==200
    assert client.get('/api/editor/drafts/999999').status_code==404


def test_scheduling_requires_approval_and_future_time(data):
    path,_,_=data
    client=TestClient(create_app(path))
    headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
    d=client.post('/api/editor/drafts',json={'kind':'plan','period':'future','body':'План'},headers=headers).json()
    url=f"/api/editor/drafts/{d['id']}/action"
    assert client.post(url,json={'action':'schedule','revision':d['revision'],'when':'2099-01-01T10:00'},headers=headers).status_code==409
    d=client.post(url,json={'action':'approved','revision':d['revision']},headers=headers).json()
    assert client.post(url,json={'action':'schedule','revision':d['revision'],'when':'2020-01-01T10:00'},headers=headers).status_code==422
    scheduled=client.post(url,json={'action':'schedule','revision':d['revision'],'when':'2099-01-01T10:00'},headers=headers)
    assert scheduled.status_code==200 and scheduled.json()['state']=='scheduled'
    undone=client.post(url,json={'action':'unschedule','revision':scheduled.json()['revision']},headers=headers)
    assert undone.status_code==200 and undone.json()['state']=='approved'


def test_glossary_avoids_recent_title():
    class Fake:pass
    gen=PostGenerator(Fake(),('acc',),Path('unused'),recent_glossary=('Флоатер',))
    for day in [date(2026,10,28)+__import__('datetime').timedelta(days=x*7) for x in range(12)]:
        d=gen.glossary(day)
        assert gen.selected_glossary_topic!='Флоатер'
        assert gen.selected_glossary_topic in d.body
