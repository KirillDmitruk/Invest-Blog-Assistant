from pathlib import Path
from fastapi.testclient import TestClient
from invest_blog.tech_store import TechDraftStore
from invest_blog.dashboard import create_app


def test_tech_store_isolated_and_revision(tmp_path):
    db=tmp_path/'app.sqlite'
    store=TechDraftStore(db)
    did=store.create('Робототехника','Текст о роботах','https://example.org/robot')
    assert store.get(did)['revision']==1
    assert store.update(did,'Роботы','Обновление','',1)
    assert not store.update(did,'Второй','Устаревшее изменение','',1)
    assert store.get(did)['body']=='Обновление'
    assert not store.delete(did,1)
    assert store.delete(did,2)
    assert store.get(did) is None
    assert not store.list_recent()


def test_tech_store_rejects_unsafe_source_and_oversized_body(tmp_path):
    import pytest
    store=TechDraftStore(tmp_path/'app.sqlite')
    with pytest.raises(ValueError):store.create('Заголовок','Текст','javascript:alert(1)')
    with pytest.raises(ValueError):store.create('Заголовок','x'*4001)


def test_new_navigation_and_tech_api(tmp_path):
    client=TestClient(create_app(tmp_path/'app.sqlite'))
    page=client.get('/')
    assert page.status_code==200 and 'page-home' in page.text and 'page-tech' in page.text
    assert client.get('/api/projects').json()['tech']['publishing_enabled'] is False
    assert client.post('/api/tech/drafts',json={'title':'Новость','body':'Текст'}).status_code==403
    token=client.get('/api/editor-token').json()['token']
    headers={'X-CSRF-Token':token}
    result=client.post('/api/tech/drafts',json={'title':'Новость','body':'Текст'},headers=headers)
    assert result.status_code==201
    draft=result.json()
    assert [x['id'] for x in client.get('/api/tech/drafts').json()]==[draft['id']]
    assert client.put('/api/tech/drafts/'+str(draft['id']),json={'title':'Новая','body':'Материал','revision':1},headers=headers).status_code==200
    assert client.put('/api/tech/drafts/'+str(draft['id']),json={'title':'Ошибка','body':'Текст','revision':1},headers=headers).status_code==409
    assert client.request('DELETE','/api/tech/drafts/'+str(draft['id']),json={'revision':2},headers=headers).status_code==204
    assert client.get('/api/tech/drafts').json()==[]
