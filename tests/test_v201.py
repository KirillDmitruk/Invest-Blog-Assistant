from pathlib import Path
from fastapi.testclient import TestClient
from invest_blog.tech_news import TechNewsStore,TechNewsCollector,parse_feed,canonical_url,category_for
from invest_blog.dashboard import create_app

RSS=b'''<?xml version="1.0"?><rss version="2.0"><channel><item><title>A new robot prototype</title><link>https://example.org/robot?utm_source=rss</link><description>&lt;b&gt;Humanoid robot&lt;/b&gt; for factories</description><pubDate>Fri, 30 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'''
ATOM=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>3D printing rocket parts</title><link rel="alternate" href="https://example.com/parts"/><summary>3D printed engines</summary><updated>2026-10-30T10:00:00Z</updated></entry></feed>'''

def test_rss_parse_and_categories():
    row=parse_feed(RSS,'Demo')[0]
    assert row['url']=='https://example.org/robot'
    assert row['category']=='robotics'
    assert '<' not in row['summary']
    assert row['published_at'].startswith('2026-10-30')
    assert parse_feed(ATOM,'Other')[0]['category']=='printing'
    assert canonical_url('http://bad.com')==''

def test_store_dedup_and_status(tmp_path):
    st=TechNewsStore(tmp_path/'news.sqlite')
    rows=parse_feed(RSS,'Demo')
    assert st.save(rows)==1
    assert st.save(rows)==0
    news=st.list_news()[0]
    assert news['category']=='robotics'
    assert st.mark(news['id'],'dismissed')
    assert st.list_news(status='dismissed')[0]['id']==news['id']
    assert st.stats()['new']==0
    st.record_source('Demo','error',error='Test unavailable')
    assert len(st.list_news())==1

def test_collector_partial_failure(tmp_path,monkeypatch):
    from invest_blog import tech_news as m
    monkeypatch.setattr(m,'FEEDS',(('Good','https://good.example/rss'),('Bad','https://bad.example/rss')))
    class Resp:
        content=RSS
        def raise_for_status(self):pass
    class Session:
        def get(self,url,**kwargs):
            if 'bad.' in url: raise m.requests.RequestException('offline')
            return Resp()
    store=TechNewsStore(tmp_path/'x.db')
    result=TechNewsCollector(store,Session()).scan()
    assert result[0]['added']==1
    assert result[1]['status']=='error'
    assert store.stats()['total']==1

def test_web_news_and_manual_draft(tmp_path):
    db=tmp_path/'x.db'
    st=TechNewsStore(db)
    st.save(parse_feed(RSS,'Demo'))
    app=create_app(db)
    with TestClient(app) as client:
        response=client.get('/api/tech/news')
        assert response.status_code==200
        item=response.json()['items'][0]
        assert client.post(f'/api/tech/news/{item["id"]}/action',json={'action':'draft'}).status_code==403
        token=client.get('/api/editor-token').json()['token']
        resp=client.post(f'/api/tech/news/{item["id"]}/action',headers={'x-csrf-token':token},json={'action':'draft'})
        assert resp.status_code==200,resp.text
        draft=resp.json()['draft']
        assert draft['source_url']==item['url']
        assert 'Перескажи' in draft['body']
        assert client.get('/api/tech/drafts').json()[0]['id']==draft['id']
