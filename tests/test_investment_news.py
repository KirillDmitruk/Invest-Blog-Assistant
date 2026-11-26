from datetime import timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from invest_blog.tech_news import TechNewsStore, FEEDS, category_for
from invest_blog.tech_direct import run, DirectQueue
from invest_blog.vk_news import compose_investment
from test_tech_direct import CLOCK, TZ, Publisher, configured, news, translate


def ru_news(path,category='russia',number=1):
    item={'source':'Банк России','title':'Банк России опубликовал новые данные по российскому финансовому рынку',
          'summary':'Регулятор представил обновлённые показатели финансового рынка и сообщил о динамике за отчётный период.',
          'url':f'https://www.cbr.ru/test/{number}','category':category,'published_at':CLOCK.astimezone(timezone.utc).isoformat()}
    TechNewsStore(path).save([item]);return item


def test_feed_list_has_no_tech_sources_and_known_investment_categories():
    names={name for name,url in FEEDS}
    assert {'Банк России','Смартлаб · новости','CNBC','ФРС США','CoinDesk','Cointelegraph','Интерфакс','РБК','SEC','The Block','Decrypt','ForkLog','Bloomberg · рынки','Financial Times · рынки'} <= names
    assert len(names)==len(FEEDS)==16
    assert dict(FEEDS)['Банк России'].endswith('/RssPress')
    assert category_for('Rates','', 'Банк России')=='russia'
    assert category_for('Bitcoin','', 'CoinDesk')=='crypto'
    assert category_for('Stocks','', 'CNBC')=='world'


def test_russian_news_does_not_need_translator(configured,monkeypatch):
    item=ru_news(configured)
    def forbidden(*a):pytest.fail('Russian news must not load or call translator')
    monkeypatch.setattr('invest_blog.tech_direct.LocalTranslator',forbidden)
    publisher=Publisher()
    assert len(run(configured,TZ,publish=True,publisher=publisher,clock=CLOCK)['sent'])==1
    assert item['title'] in publisher.messages[0] and item['url'] in publisher.messages[0]


def test_long_headline_only_news_is_honest_without_invented_body():
    item={'title':'Банк России опубликовал новые данные по российскому финансовому рынку',
          'summary':'','source':'Банк России','url':'https://cbr.ru/test','category':'russia'}
    text=compose_investment(item)
    assert item['title'] in text and 'Источник:' in text


def test_legacy_tech_news_never_enters_active_list_or_auto_queue(configured):
    news(configured)
    with DirectQueue(configured).connect() as db:db.execute("UPDATE tech_news SET category='robotics'")
    assert TechNewsStore(configured).list_news()==[]
    assert TechNewsStore(configured).stats()['new']==0
    publisher=Publisher()
    assert run(configured,TZ,publish=True,publisher=publisher,clock=CLOCK)['sent']==[]


def test_pause_and_topic_selection_survive_reload(configured):
    store=TechNewsStore(configured);state=store.controls()
    store.set_controls(False,['russia'],state['revision'])
    assert TechNewsStore(configured).controls()['enabled'] is False
    ru_news(configured)
    publisher=Publisher()
    assert run(configured,TZ,publish=True,publisher=publisher,clock=CLOCK)['sent']==[]
    state=store.controls();store.set_controls(True,['crypto'],state['revision'])
    assert run(configured,TZ,publish=True,publisher=publisher,clock=CLOCK)['sent']==[]
    assert publisher.messages==[]


def test_pause_during_translation_blocks_sending(configured):
    news(configured);store=TechNewsStore(configured)
    def pause(text):
        state=store.controls();store.set_controls(False,state['categories'],state['revision'])
        return translate(text)
    publisher=Publisher()
    assert run(configured,TZ,publish=True,publisher=publisher,translator=pause,clock=CLOCK)['sent']==[]
    assert not publisher.messages


def test_category_rotation_avoids_crypto_only_feed(configured):
    ru_news(configured)
    news(configured,1)  # Distinct URL from the Russian item.
    publisher=Publisher()
    run(configured,TZ,publish=True,publisher=publisher,translator=translate,clock=CLOCK)
    run(configured,TZ,publish=True,publisher=publisher,translator=translate,clock=CLOCK+timedelta(minutes=30))
    assert set(DirectQueue(configured).category_counts(CLOCK.date().isoformat()))=={'russia','world'}


def test_manual_source_controls_and_csrf(configured,monkeypatch):
    from invest_blog.dashboard import create_app
    with TestClient(create_app(configured)) as client:
        assert client.post('/api/news/refresh').status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        state=client.get('/api/news/controls').json()
        assert [s['name'] for s in state['telegram_sources']]==['ne_investor','crypton_off','roflpuls','kokorevinvest']
        body={k:state[k] for k in ('revision','enabled','categories')}
        assert client.put('/api/news/controls',json=body).status_code==403
        assert client.put('/api/news/controls',headers=headers,json={**body,'categories':[]}).status_code==422
        assert client.put('/api/news/controls',headers=headers,json={**body,'enabled':False}).status_code==200
        assert client.put('/api/news/controls',headers=headers,json=body).status_code==409
        item={'url':'https://t.me/ne_investor/123','title':'Новые данные финансового рынка России',
              'summary':'Проверенный авторский пересказ новости финансового рынка с указанием исходного сообщения и без копирования чужого текста.','category':'russia'}
        assert client.post('/api/news',json=item).status_code==403
        assert client.post('/api/news',headers=headers,json={**item,'url':'https://t.me/ne_investor'}).status_code==422
        assert client.post('/api/news',headers=headers,json=item).status_code==201
        assert client.post('/api/news',headers=headers,json=item).status_code==409
        assert TechNewsStore(configured).list_news()[0]['source']=='Telegram · @ne_investor'

@pytest.mark.parametrize('source', ['Интерфакс','РБК','Коммерсантъ · экономика'])
def test_added_russian_feeds_classify_the_actual_story(source):
    assert category_for('Рост прибыли Сбербанка','',source)=='russia'
    assert category_for('США: акции Nvidia подорожали','',source)=='world'
    assert category_for('Биткоин вырос','',source)=='crypto'

@pytest.mark.parametrize('source',['Bloomberg · рынки','Financial Times · рынки','SEC'])
def test_crypto_story_from_global_publisher_is_crypto(source):
    assert category_for('Bitcoin ETF securities approval','',source)=='crypto'
