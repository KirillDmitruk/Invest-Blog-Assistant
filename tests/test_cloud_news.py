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
    assert store.state['pending_news'] and not sender.sent
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

def test_preview_can_use_new_channel_with_old_slot_present():
    store=Store();store.state['deliveries'][str(int(NOW.timestamp())//1800)]={'period':'old','status':'sent','source_url':'https://old.example/news','title_key':'old','day':NOW.date().isoformat(),'category':'russia'}
    assert publish(store,None,[item()],editor=editor,now=NOW,target='@newinvest')['status']=='preview'

def test_official_press_releases_are_not_mistaken_for_paid_advertising():
    from invest_blog.cloud_news import fresh
    release=item();release.update(source='SEC',title='SEC securities press release',summary='The regulator published a press release on securities and financial market regulation today.')
    assert fresh([release],NOW)==[release]
    release['source']='Unknown paid wire'
    assert fresh([release],NOW)==[]
    release.update(source='SEC',summary='Sponsored financial market press release with sufficiently long promotional text for this test.')
    assert fresh([release],NOW)==[]


def test_source_failure_does_not_discard_working_feeds_and_reports_real_eligibility(monkeypatch):
    import invest_blog.cloud_news as module
    monkeypatch.setattr(module,'FEEDS',[('Bad','https://bad.example/rss'),('Good','https://good.example/rss'),('Empty','https://empty.example/rss')])
    class Response:
        def __init__(self,url):self.url=url
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def raise_for_status(self):
            if 'bad.' in self.url:raise ValueError('Unavailable')
        def iter_content(self,size):return [b'empty' if 'empty.' in self.url else b'good']
    class Session:
        def get(self,url,**kwargs):
            assert 'User-Agent' in kwargs['headers']
            return Response(url)
    monkeypatch.setattr(module,'parse_feed',lambda raw,name:[] if raw==b'empty' else [item()])
    items,status=module.collect(Session(),NOW)
    assert len(items)==1 and [x['ok'] for x in status]==[False,True,False]
    assert status[1]['found']==status[1]['eligible']==1
    assert status[1]['latest_at']==item()['published_at']

def test_faster_post_after_ten_minutes_and_no_burst_across_slot_boundary():
    store=Store();sender=Sender(store)
    assert publish(store,sender,[item()],editor=editor,now=NOW,send=True)['status']=='sent'
    assert publish(store,sender,[item(2)],editor=editor,now=NOW+timedelta(minutes=5),send=True)['status']=='already_attempted'
    assert publish(store,sender,[],editor=editor,now=NOW+timedelta(minutes=10),send=True)['status']=='sent'
    assert len(sender.sent)==2

def test_observed_story_survives_disappearing_from_rss_and_preview_is_readonly():
    store=Store();sender=Sender(store)
    assert publish(store,sender,[item(1),item(2,'crypto')],editor=editor,now=NOW,send=True)['status']=='sent'
    snapshot=deepcopy(store.state)
    assert publish(store,None,[],editor=editor,now=NOW+timedelta(minutes=10))['status']=='preview'
    assert store.state==snapshot
    assert publish(store,sender,[],editor=editor,now=NOW+timedelta(minutes=10),send=True)['status']=='sent'
    assert len(sender.sent)==2

def test_pending_stories_expire_and_old_half_hour_delivery_blocks_early_send():
    store=Store();sender=Sender(store)
    store.state['deliveries']['legacy-halfhour']={'period':'legacy-halfhour','created_at':NOW.isoformat(),'day':NOW.date().isoformat(),'status':'sent','source_url':item()['url'],'title_key':fingerprint(item()),'category':'russia'}
    assert publish(store,sender,[item(2)],editor=editor,now=NOW+timedelta(minutes=5),send=True)['status']=='already_attempted'
    result=publish(store,sender,[],editor=editor,now=NOW+timedelta(hours=23,minutes=5),send=True)
    assert result['status']=='no_suitable_news' and not store.state['pending_news']
