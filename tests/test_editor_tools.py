"""Shared writing tools respect local editor permissions and never mutate drafts."""
from fastapi.testclient import TestClient

from invest_blog.dashboard import create_app
from invest_blog.tech_store import TechDraftStore


def test_translation_requires_editor_and_validates_size(tmp_path):
    with TestClient(create_app(tmp_path / 'app.db')) as client:
        assert client.post('/api/editor/tools/translate', json={'text': 'Hello'}).status_code == 403
        token = client.get('/api/editor-token').json()['token']
        headers = {'X-CSRF-Token': token}
        assert client.post('/api/editor/tools/translate', headers={**headers, 'Origin':'https://evil.example'}, json={'text':'Hello'}).status_code == 403
        for text in ['', 'a' * 10001]:
            assert client.post('/api/editor/tools/translate', headers=headers, json={'text':text}).status_code == 422


def test_translation_previews_without_modifying_saved_material(tmp_path, monkeypatch):
    from invest_blog import vk_news
    path = tmp_path / 'app.db'
    drafts = TechDraftStore(path)
    draft_id = drafts.create('Robot', 'A robot helps makers.')
    monkeypatch.setattr(vk_news, 'LocalTranslator', lambda: lambda text: 'Робот помогает мастерам.')
    with TestClient(create_app(path)) as client:
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        response=client.post('/api/editor/tools/translate',headers=headers,json={'text':'A robot helps makers.'})
        assert response.status_code==200
        assert response.json()['text']=='Робот помогает мастерам.'
        assert drafts.get(draft_id)['body']=='A robot helps makers.'
        assert drafts.get(draft_id)['revision']==1


def test_missing_translation_model_is_actionable_and_static_assets_available(tmp_path, monkeypatch):
    from invest_blog import vk_news
    def unavailable():
        raise RuntimeError('Не найдена локальная модель перевода en-ru')
    monkeypatch.setattr(vk_news, 'LocalTranslator', unavailable)
    with TestClient(create_app(tmp_path/'app.db')) as client:
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        result=client.post('/api/editor/tools/translate',headers=headers,json={'text':'Hello'})
        assert result.status_code==503
        assert 'en-ru' in result.json()['detail']
        assert client.get('/static/workspace.js').status_code==200
        assert client.get('/static/workspace.css').status_code==200
