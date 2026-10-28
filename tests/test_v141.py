"""Regression tests for v1.4.1 schedule widget and editorial queue removal."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from invest_blog.dashboard import create_app
from invest_blog.model import Draft
from invest_blog.publication_schedule import recurring_schedule, WEEKLY_RULES
from invest_blog.storage import DraftStore
from invest_blog.portfolio_store import PortfolioStore
from invest_blog.editorial import ContentPlanner


@pytest.fixture
def app_data(tmp_path: Path):
    db = tmp_path / "blog.db"
    portfolio = PortfolioStore(db)
    store = DraftStore(db)
    app = create_app(db, timezone=ZoneInfo('Europe/Moscow'), times={
        'plan': (8, 35), 'paper': (10, 10), 'glossary': (14, 0),
        'weekly': (9, 45), 'monthly': (11, 5),
    })
    client = TestClient(app)
    csrf = client.get('/api/editor-token').json()['token']
    return store, portfolio, client, {'X-CSRF-Token': csrf}


def test_routine_has_all_seven_days_with_real_config(app_data):
    _, _, client, _ = app_data
    response = client.get('/api/publication-routine')
    assert response.status_code == 200
    routine = response.json()
    assert routine['timezone'] == 'Europe/Moscow'
    weekdays = routine['weekdays']
    assert [x['name'] for x in weekdays] == [
        'Понедельник', 'Вторник', 'Среда', 'Четверг',
        'Пятница', 'Суббота', 'Воскресенье',
    ]
    assert [e['time'] for e in weekdays[0]['items']] == ['08:35']
    assert [e['time'] for e in weekdays[2]['items']] == ['10:10']
    assert [e['time'] for e in weekdays[4]['items']] == ['14:00']
    assert [e['time'] for e in weekdays[5]['items']] == ['09:45']
    assert not weekdays[1]['items'] and not weekdays[6]['items']
    assert routine['monthly'] == {
        'day': 1, 'kind': 'monthly', 'label': 'Ежемесячный отчёт', 'time': '11:05'
    }
    assert len(WEEKLY_RULES) == 4
    assert 'черновиков' in routine['note']


def test_routine_defaults():
    routine = recurring_schedule()
    assert routine['weekdays'][0]['items'][0]['time'] == '10:00'
    assert routine['monthly']['time'] == '11:00'


def test_delete_removes_from_queue_and_unsent_and_is_idempotently_suppressed(app_data):
    store, portfolio, client, headers = app_data
    did, _ = store.insert(Draft('plan', '2026-11-02', 'Черновик для понедельника'), scheduled=True)
    assert store.unsent()
    assert store.change_state(did, 'approved')
    assert store.queue_publish(did)
    revision = store.get(did)['revision']
    # httpx's TestClient accepts method-agnostic JSON via request.
    result = client.request('DELETE', f'/api/editor/drafts/{did}', json={'revision': revision}, headers=headers)
    assert result.status_code == 204
    assert store.get(did)['state'] == 'deleted'
    assert store.pending_publications() == []
    assert store.unsent() == []
    assert all(r['id'] != did for r in store.list_recent())
    assert all(r['id'] != did for r in client.get('/api/drafts').json())
    assert client.get(f'/api/editor/drafts/{did}').status_code == 404
    # Recurring job won't regenerate this intentionally removed post.
    same_id, newly_created = store.insert(Draft('plan', '2026-11-02', 'Новый текст'), scheduled=True)
    assert same_id == did and newly_created is False
    assert store.get(did)['state'] == 'deleted'
    day = ContentPlanner(portfolio, store).calendar(__import__('datetime').date(2026,11,2), 1)[0]
    assert day['state'] == 'deleted'


def test_delete_scheduled_cancels_publication(app_data):
    store, _, client, headers = app_data
    did = store.create_manual('paper', 'week-1', 'На публикацию')
    assert store.change_state(did, 'approved')
    assert store.schedule(did, '2099-12-31T00:00:00+00:00')
    assert store.due_scheduled('2100-01-01T00:00:00+00:00')
    rev = store.get(did)['revision']
    response = client.request('DELETE', f'/api/editor/drafts/{did}', headers=headers, json={'revision': rev})
    assert response.status_code == 204
    assert store.get(did)['scheduled_at'] is None
    assert store.due_scheduled('2100-01-01T00:00:00+00:00') == []
    assert not store.claim_publish(did, scheduled=True)


def test_delete_protects_published_and_concurrent_revision(app_data):
    store, _, client, headers = app_data
    d1 = store.create_manual('paper', 'week-1', 'Пост')
    initial = store.get(d1)['revision']
    assert store.change_state(d1, 'approved')
    stale = client.request('DELETE', f'/api/editor/drafts/{d1}', headers=headers, json={'revision': initial})
    assert stale.status_code == 409
    assert store.get(d1)['state'] == 'approved'
    assert store.claim_publish(d1)
    assert client.request('DELETE', f'/api/editor/drafts/{d1}', headers=headers,
                          json={'revision': store.get(d1)['revision']}).status_code == 409
    store.publish_success(d1, 555)
    assert client.request('DELETE', f'/api/editor/drafts/{d1}', headers=headers,
                          json={'revision': store.get(d1)['revision']}).status_code == 409
    assert store.get(d1)['state'] == 'published'


def test_delete_rejects_bad_auth_and_bad_revision(app_data):
    store, _, client, headers = app_data
    did = store.create_manual('glossary', 'week-1', 'Объяснение')
    url = f'/api/editor/drafts/{did}'
    assert client.request('DELETE', url, json={'revision': 1}).status_code == 403
    assert client.request('DELETE', url, headers=headers, json={'revision': '1'}).status_code == 422
    assert client.request('DELETE', url, headers={**headers, 'Origin': 'https://evil.example'},
                          json={'revision': 1}).status_code == 403
    assert client.request('DELETE', '/api/editor/drafts/999999', headers=headers,
                          json={'revision': 1}).status_code == 404
    assert store.get(did)['state'] != 'deleted'


def test_html_has_weekly_routine_and_delete_control(app_data):
    _, _, client, _ = app_data
    response = client.get('/')
    assert response.status_code == 200
    assert 'weeklyRoutine' in response.text
    assert 'monthlyRoutine' in response.text
    assert 'deleteEditorDraft' in response.text
    assert '/api/publication-routine' in response.text


def test_removed_draft_cannot_be_resurrected_by_telegram_edit_or_manual_done(app_data):
    store, _, _, _ = app_data
    did = store.create_manual('paper', 'test', 'Текст')
    assert store.delete_from_queue(did, store.get(did)['revision'])
    with pytest.raises(ValueError):
        store.set_body(did, 'Новый текст')
    with pytest.raises(RuntimeError):
        store.update_body(did, 'Новая версия', store.get(did)['revision'])
    store.mark_done(did)
    assert store.get(did)['state'] == 'deleted'
