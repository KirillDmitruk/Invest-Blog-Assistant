from fastapi.testclient import TestClient
from invest_blog.dashboard import create_app
from invest_blog.tech_publish_dashboard import overview, dispatch
from invest_blog.vk_news import VKNewsQueue


def test_status_empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv('VK_AUTO_PUBLISH','false')
    monkeypatch.setenv('TECH_TG_AUTO_PUBLISH','false')
    state=overview(tmp_path/'test.sqlite',None)
    assert state['today']=={'vk':0,'telegram':0}
    assert state['next_at'] is None
    assert state['history']==[]


def test_publish_action_requires_csrf_confirmation_and_feature_flag(tmp_path,monkeypatch):
    monkeypatch.setenv('VK_AUTO_PUBLISH','false')
    client=TestClient(create_app(tmp_path/'test.sqlite'))
    assert client.get('/api/tech/publishing').status_code==200
    assert client.post('/api/tech/publishing/now',json={'confirm':'PUBLISH'}).status_code==403
    token=client.get('/api/editor-token').json()['token']
    headers={'X-CSRF-Token':token}
    assert client.post('/api/tech/publishing/now',json={'confirm':'no'},headers=headers).status_code==422
    assert client.post('/api/tech/publishing/now',json={'confirm':'PUBLISH'},headers=headers).status_code==409


def test_dispatch_tg_even_on_vk_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('TECH_TG_MODE','crosspost')
    from invest_blog import tech_publish_dashboard as mod
    monkeypatch.setenv('VK_AUTO_PUBLISH','true')
    monkeypatch.setenv('TECH_TG_AUTO_PUBLISH','true')
    def fail(*args,**kwargs): raise RuntimeError('VK temporary issue')
    monkeypatch.setattr(mod,'publish_vk',fail)
    monkeypatch.setattr(mod,'publish_tg',lambda *args,**kwargs:{'sent':[{'news_id':1}]})
    result=dispatch(tmp_path/'test.sqlite',None)
    assert result['vk']['errors']
    assert len(result['telegram']['sent'])==1
