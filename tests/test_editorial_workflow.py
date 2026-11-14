from datetime import date, timedelta
import sqlite3
from types import SimpleNamespace

import pytest
from invest_blog.editorial_workflow import EditorialWorkflow
from invest_blog.storage import DraftStore
from invest_blog.model import Draft


def test_ideas_reserve_rollover_and_no_repeats(tmp_path,monkeypatch):
    p=tmp_path/'test.db'
    DraftStore(p)
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE editorial_topics(kind TEXT,topic_key TEXT,draft_id INTEGER)')
    workflow=EditorialWorkflow(p)
    monkeypatch.setattr(workflow,'_find_online_titles',lambda:('https://example.org/rss','облигации купон риск доходность'))
    now=date(2026,11,2)
    initial=workflow.refresh_ideas(now)
    assert len(initial)==3
    workflow.select(initial[0]['topic'],now)
    with pytest.raises(ValueError):workflow.select(initial[1]['topic'],now)
    other=workflow.refresh_ideas(now+timedelta(days=7))
    assert len(other)==3
    assert {r['topic'] for r in initial[1:]}.issubset({r['topic'] for r in other})
    assert initial[0]['topic'] not in {r['topic'] for r in other}
    assert len({r['topic'] for r in other})==3


def test_done_preserves_draft_and_marks_calendar(tmp_path):
    from invest_blog.editorial import ContentPlanner
    p=tmp_path/'test.db'
    store=DraftStore(p)
    workflow=EditorialWorkflow(p)
    day=date(2026,11,6)
    period='2026-11-02'
    did,_=store.insert(Draft('glossary',period,'Мой текст'),scheduled=True)
    assert workflow.progress(day.isoformat(),'glossary','in_progress')=='in_progress'
    assert workflow.progress(day.isoformat(),'glossary','done')=='done'
    assert store.get(did)['body']=='Мой текст'
    assert workflow.states()[(day.isoformat(),'glossary')]=='done'
    assert not any(r['kind']=='glossary' for r in ContentPlanner(None,store).calendar(day,days=1))


def test_done_cancels_scheduled_publication_without_deleting_draft(tmp_path):
    p=tmp_path/'test.db'
    store=DraftStore(p)
    workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','Не отправлять автоматически'),scheduled=True)
    with store._connect() as db:
        db.execute("UPDATE drafts SET state='scheduled',scheduled_at=? WHERE id=?",
                   ('2026-11-06T10:00:00+00:00',did))
        db.execute("INSERT INTO editorial_publish_requests(draft_id,revision,requested_at) VALUES(?,?,?)",
                   (did,1,'2026-11-05T10:00:00+00:00'))
    workflow.progress('2026-11-06','glossary','done')
    row=store.get(did)
    assert row['body']=='Не отправлять автоматически'
    assert row['state']=='review'
    assert row['scheduled_at'] is None
    assert row['revision']==2
    assert store.due_scheduled('2099-01-01T00:00:00+00:00')==[]
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM editorial_publish_requests WHERE draft_id=?',(did,)).fetchone()[0]==0


def test_done_cancels_queued_approved_publication(tmp_path):
    p=tmp_path/'test.db'
    store=DraftStore(p)
    workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','Сохранённый пост'),scheduled=True)
    with store._connect() as db:
        db.execute("UPDATE drafts SET state='approved' WHERE id=?",(did,))
        db.execute("INSERT INTO editorial_publish_requests(draft_id,revision,requested_at) VALUES(?,?,?)",
                   (did,1,'2026-11-05T10:00:00+00:00'))
    workflow.progress('2026-11-06','glossary','done')
    assert store.get(did)['state']=='approved'
    assert store.pending_publications()==[]


def test_done_rejects_stale_revision_without_changing_schedule(tmp_path):
    p=tmp_path/'test.db'
    store=DraftStore(p)
    workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','Текст'),scheduled=True)
    with pytest.raises(ValueError,match='изменился'):
        workflow.progress('2026-11-06','glossary','done',revision=999)
    assert store.get(did)['body']=='Текст'
    assert ('2026-11-06','glossary') not in workflow.states()


def test_published_draft_can_be_marked_done_without_modification(tmp_path):
    p=tmp_path/'test.db';store=DraftStore(p);workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','Опубликованный текст'),scheduled=True)
    with store._connect() as db:
        db.execute("UPDATE drafts SET state='published' WHERE id=?",(did,))
        db.execute('INSERT INTO telegram_messages(message_id,draft_id) VALUES(?,?)',(123,did))
    assert workflow.progress('2026-11-06','glossary','done')=='done'
    assert store.get(did)['state']=='published'
    assert store.draft_for_message(123)==did


def test_publishing_draft_cannot_be_marked_done(tmp_path):
    p=tmp_path/'test.db';store=DraftStore(p);workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','В процессе отправки'),scheduled=True)
    with store._connect() as db:
        db.execute("UPDATE drafts SET state='publishing' WHERE id=?",(did,))
    with pytest.raises(ValueError,match='отправляется'):
        workflow.progress('2026-11-06','glossary','done')
    assert store.get(did)['state']=='publishing'
    assert ('2026-11-06','glossary') not in workflow.states()
