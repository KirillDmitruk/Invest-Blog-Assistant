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


def test_done_deletes_draft_and_marks_calendar(tmp_path):
    from invest_blog.editorial import ContentPlanner
    p=tmp_path/'test.db'
    store=DraftStore(p)
    workflow=EditorialWorkflow(p)
    day=date(2026,11,6)
    period='2026-11-02'
    did,_=store.insert(Draft('glossary',period,'text'),scheduled=True)
    assert store.get(did)
    assert workflow.progress(day.isoformat(),'glossary','in_progress')=='in_progress'
    assert workflow.progress(day.isoformat(),'glossary','done')=='done'
    assert store.get(did) is None
    assert workflow.states()[(day.isoformat(),'glossary')]=='done'
    assert not any(r['kind']=='glossary' for r in ContentPlanner(None,store).calendar(day,days=1))


def test_published_cannot_be_deleted(tmp_path):
    p=tmp_path/'test.db';store=DraftStore(p);workflow=EditorialWorkflow(p)
    did,_=store.insert(Draft('glossary','2026-11-02','text'),scheduled=True)
    with store._connect() as db:db.execute("UPDATE drafts SET state='published' WHERE id=?",(did,))
    with pytest.raises(ValueError):workflow.progress('2026-11-06','glossary','done')
    assert store.get(did)
