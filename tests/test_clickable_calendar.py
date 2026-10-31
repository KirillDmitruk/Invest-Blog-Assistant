"""Click-to-generate calendar drafts, with no real broker requests."""
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient
from invest_blog.dashboard import create_app
from invest_blog.editorial import ContentPlanner
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.storage import DraftStore
from invest_blog.model import Draft


def next_weekday(day, wd):
    return day + timedelta(days=(wd-day.weekday())%7)


def test_calendar_click_creates_once_and_preserves_edits(tmp_path,monkeypatch):
    path=tmp_path/'drafts.db'
    app=create_app(path)
    client=TestClient(app)
    tok=client.get('/api/editor-token').json()['token']
    headers={'X-CSRF-Token':tok}
    day=next_weekday(date.today(),4).isoformat()
    assert client.post('/api/editor/calendar-draft',json={'day':day,'kind':'glossary'}).status_code==403
    first=client.post('/api/editor/calendar-draft',json={'day':day,'kind':'glossary'},headers=headers)
    assert first.status_code==200,first.text
    assert first.json()['created'] is True
    row=first.json()['draft']
    assert row['body'].startswith('📚 Ликбез')
    saved=client.put(f"/api/editor/drafts/{row['id']}",json={'body':'Мой отредактированный текст','revision':row['revision']},headers=headers)
    assert saved.status_code==200
    second=client.post('/api/editor/calendar-draft',json={'day':day,'kind':'glossary'},headers=headers)
    assert second.status_code==200
    assert second.json()['created'] is False
    assert second.json()['draft']['id']==row['id']
    assert second.json()['draft']['body']=='Мой отредактированный текст'
    assert client.post('/api/editor/calendar-draft',json={'day':day,'kind':'weekly'},headers=headers).status_code==422


def test_calendar_weekly_existing_generator_period(tmp_path):
    path=tmp_path/'drafts.db'
    store=DraftStore(path)
    day=next_weekday(date.today(),5)
    from invest_blog.posts import previous_workweek
    a,b=previous_workweek(day)
    did,_=store.insert(Draft('weekly',f'{a.isoformat()}_{b.isoformat()}','Уже подготовлено'),scheduled=True)
    found=next(x for x in ContentPlanner(PortfolioStore(path),store).calendar(date.today()) if x['kind']=='weekly' and x['day']==day.isoformat())
    assert found['draft_id']==did
    client=TestClient(create_app(path))
    headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
    resp=client.post('/api/editor/calendar-draft',json={'day':day.isoformat(),'kind':'weekly'},headers=headers)
    assert resp.status_code==200
    assert resp.json()['draft']['id']==did and not resp.json()['created']
