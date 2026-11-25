from copy import deepcopy
from datetime import datetime,timedelta,timezone
import pytest
from invest_blog.cloud_news import publish,slot,fingerprint

NOW=datetime(2026,11,25,10,5,tzinfo=timezone.utc)
def item(n=1,category='russia',hours=1):return {'title':'Компания опубликовала отчёт '+str(n),'summary':'Компания сообщила о росте выручки и представила результаты работы за отчётный период.','source':'Example','url':'https://example.org/'+str(n),'category':category,'published_at':(NOW-timedelta(hours=hours)).isoformat()}
def editor(news,**kwargs):return {'headline':'Компания представила результаты работы','paragraphs':['Выручка выросла. Подробности раскрыты в опубликованном отчёте компании.']}
class Store:
    def __init__(self):self.state={'version':1,'deliveries':{},'papers':[],'glossary':[]};self.saves=0
    def load(self):return deepcopy(self.state)
    def save(self,state):self.saves+=1;self.state=deepcopy(state)
class Sender:
    def __init__(self,store,fail=False):self.store=store;self.fail=fail;self.sent=[]
    def send(self,text):
        assert any(r['status']=='attempted' for r in self.store.state['deliveries'].values())
        self.sent.append(text)
        if self.fail:raise TimeoutError()
        return 'https://t.me/kirintechnews/1'
def test_hours():
    assert slot(NOW.replace(hour=5)) is None
    assert slot(NOW.replace(hour=6)) is not None
    assert slot(NOW.replace(hour=20)) is None
    assert slot(NOW.replace(hour=19,minute=30)) is not None
def test_preview_never_reserves_or_sends():
    store=Store();sender=Sender(store)
    assert publish(store,sender,[item()],editor=editor,now=NOW)['status']=='preview'
    assert not sender.sent and store.saves==0
def test_claim_precedes_send_and_same_slot_never_repeats():
    store=Store();sender=Sender(store)
    assert publish(store,sender,[item()],editor=editor,now=NOW,send=True)['status']=='sent'
    assert publish(store,sender,[item(2)],editor=editor,now=NOW,send=True)['status']=='already_attempted'
    assert len(sender.sent)==1
def test_ambiguous_send_not_repeated_in_later_slot():
    store=Store();sender=Sender(store,True)
    assert publish(store,sender,[item()],editor=editor,now=NOW,send=True)['status']=='uncertain'
    assert publish(store,sender,[item()],editor=editor,now=NOW+timedelta(minutes=30),send=True)['status']=='no_suitable_news'
    assert len(sender.sent)==1
def test_stale_future_empty_and_bad_editor_do_not_send():
    store=Store();sender=Sender(store)
    assert publish(store,sender,[item(hours=25),item(hours=-1)],editor=editor,now=NOW,send=True)['status']=='no_suitable_news'
    def broken(*a,**kw):raise ValueError()
    assert publish(store,sender,[item()],editor=broken,now=NOW,send=True)['status']=='no_suitable_news'
    assert store.saves==0 and not sender.sent
def test_fair_rotation_prefers_another_available_category():
    store=Store();sender=Sender(store)
    publish(store,sender,[item()],editor=editor,now=NOW,send=True)
    result=publish(store,sender,[item(2),item(3,'crypto')],editor=editor,now=NOW+timedelta(minutes=30),send=True)
    assert result['category']=='crypto'
def test_same_headline_at_different_url_is_not_reposted():
    store=Store();sender=Sender(store)
    publish(store,sender,[item()],editor=editor,now=NOW,send=True)
    duplicate=item();duplicate['url']='https://other.example/story'
    assert publish(store,sender,[duplicate],editor=editor,now=NOW+timedelta(minutes=30),send=True)['status']=='no_suitable_news'
def test_failed_reservation_prevents_telegram_request():
    class Broken(Store):
        def save(self,state):raise ValueError('State unavailable')
    store=Broken();sender=Sender(store)
    with pytest.raises(ValueError):publish(store,sender,[item()],editor=editor,now=NOW,send=True)
    assert not sender.sent

def test_politics_and_advertising_without_market_facts_are_skipped():
    store=Store();sender=Sender(store)
    political=item();political.update(title='Переговоры государств продолжатся завтра',summary='Делегации обсудили дипломатические предложения и договорились продолжить переговоры на следующей встрече.')
    ad=item(2);ad['summary']+=' Партнёрский материал.'
    assert publish(store,sender,[political,ad],editor=editor,now=NOW,send=True)['status']=='no_suitable_news'
    assert not sender.sent

def test_missing_article_does_not_starve_other_categories():
    store=Store();sender=Sender(store)
    def enrich(news):
        if news['category']=='russia':raise ValueError('Article unavailable')
        return news
    result=publish(store,sender,[item(),item(2,'world')],editor=editor,enricher=enrich,now=NOW,send=True)
    assert result['status']=='sent' and result['category']=='world'

def test_target_channels_have_distinct_slot_reservations():
    store=Store();first=Sender(store);first.channel='@firstinvest';second=Sender(store);second.channel='@secondinvest'
    assert publish(store,first,[item()],editor=editor,now=NOW,send=True)['status']=='sent'
    assert publish(store,second,[item(2)],editor=editor,now=NOW,send=True)['status']=='sent'
    assert {r['channel'] for r in store.state['deliveries'].values()}=={'@firstinvest','@secondinvest'}
