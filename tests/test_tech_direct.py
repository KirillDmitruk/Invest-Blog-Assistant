"""Independent half-hour Tech Telegram delivery; never calls a real publisher."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
import requests

from invest_blog.tech_direct import DirectQueue, DirectSchedule, run
from invest_blog.tech_news import TechNewsStore
from invest_blog.tech_telegram import TechTelegramPublisher

TZ = ZoneInfo('Europe/Moscow')
CLOCK = datetime(2026, 11, 14, 9, tzinfo=TZ)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for key, value in {'TECH_TG_MODE':'direct', 'TECH_TG_RUNNER':'bot', 'TECH_TG_AUTO_PUBLISH':'true',
                       'TECH_TG_START_HOUR':'9', 'TECH_TG_END_HOUR':'23',
                       'TECH_TG_DAILY_LIMIT':'28', 'TECH_TG_MAX_AGE_HOURS':'24',
                       'VK_AUTO_PUBLISH':'false', 'TELEGRAM_CHANNEL_ID':'',
                       'TECH_TELEGRAM_CHANNEL_ID':'@testtech'}.items():
        monkeypatch.setenv(key, value)
    return tmp_path/'news.db'


def news(path, n=1, title=None, published=None, summary=None):
    TechNewsStore(path).save([dict(source='Example', title=title or f'New robot model number {n}',
        url=f'https://example.org/{n}', summary=summary if summary is not None else 'A company announced a new robotic device for factory workers with a range of features described in the announcement.',
        category='world', published_at=(published or CLOCK-timedelta(hours=1)).astimezone(timezone.utc).isoformat())])


def translate(text):
    return 'Переведённый текст новости: ' + text


class Publisher:
    channel_id = '-100123'
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail
    def send(self, message):
        self.messages.append(message)
        if self.fail:
            raise requests.Timeout('ambiguous')
        return 'https://t.me/testtech/1'


def test_preview_has_no_delivery_side_effects(configured, monkeypatch):
    path = configured
    monkeypatch.setenv('TECH_TG_AUTO_PUBLISH', 'false')
    news(path)
    publisher = Publisher()
    result = run(path, TZ, translator=translate, publisher=publisher, clock=CLOCK)
    assert len(result['prepared']) == 1
    assert publisher.messages == []
    q = DirectQueue(path)
    with q.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM tech_telegram_publications').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM tech_tg_direct_claims').fetchone()[0] == 0
    assert q.last_run() is None


def test_direct_publish_does_not_need_vk_and_reserves_one_slot(configured):
    path = configured
    news(path, 1); news(path, 2)
    publisher = Publisher()
    first = run(path, TZ, publish=True, translator=translate, publisher=publisher, clock=CLOCK)
    second = run(path, TZ, publish=True, translator=translate, publisher=publisher, clock=CLOCK+timedelta(minutes=1))
    assert len(first['sent']) == 1
    assert second['sent'] == []
    assert len(publisher.messages) == 1
    third = run(path, TZ, publish=True, translator=translate, publisher=publisher, clock=CLOCK+timedelta(minutes=30))
    assert len(third['sent']) == 1
    with DirectQueue(path).connect() as db:
        assert db.execute('SELECT COUNT(*) FROM vk_news_publications').fetchone()[0] == 0


def test_stale_future_dismissed_and_incomplete_news_are_skipped(configured):
    path = configured
    news(path, 1, published=CLOCK-timedelta(hours=25))
    news(path, 2, published=CLOCK+timedelta(hours=1))
    news(path, 3, summary='Too short')
    news(path, 4)
    with DirectQueue(path).connect() as db:
        db.execute("UPDATE tech_news SET status='dismissed' WHERE url='https://example.org/4'")
    publisher = Publisher()
    result = run(path, TZ, publish=True, translator=translate, publisher=publisher, clock=CLOCK)
    assert result['sent'] == [] and publisher.messages == []


def test_identity_translation_is_never_published(configured):
    news(configured)
    publisher = Publisher()
    assert run(configured,TZ,publish=True,translator=lambda x:x,publisher=publisher,clock=CLOCK)['sent'] == []
    assert publisher.messages == []


def test_duplicate_title_across_different_urls_is_not_reposted(configured):
    news(configured,1,title='A new robot has been announced')
    news(configured,2,title='A NEW robot has been announced!')
    publisher = Publisher()
    run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK)
    result=run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK+timedelta(minutes=30))
    assert result['sent'] == [] and len(publisher.messages)==1


def test_daily_cap_and_hours_are_enforced(configured, monkeypatch):
    monkeypatch.setenv('TECH_TG_DAILY_LIMIT','1')
    news(configured,1);news(configured,2)
    publisher = Publisher()
    assert run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK.replace(hour=8))['sent']==[]
    assert len(run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK)['sent'])==1
    assert run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK+timedelta(minutes=30))['sent']==[]
    assert run(configured,TZ,publish=True,translator=translate,publisher=publisher,clock=CLOCK.replace(hour=23))['sent']==[]


def test_uncertain_send_is_not_retried_even_after_restart(configured):
    news(configured)
    failing = Publisher(fail=True)
    result=run(configured,TZ,publish=True,translator=translate,publisher=failing,clock=CLOCK)
    assert result['errors']
    replacement=Publisher()
    assert run(configured,TZ,publish=True,translator=translate,publisher=replacement,clock=CLOCK+timedelta(minutes=30))['sent']==[]
    assert replacement.messages == []
    with DirectQueue(configured).connect() as db:
        assert db.execute('SELECT status FROM tech_telegram_publications').fetchone()[0]=='uncertain'


def test_parallel_process_reservations_allow_only_one_post_per_slot(configured):
    news(configured,1);news(configured,2)
    queue=DirectQueue(configured)
    candidates=queue.candidates(CLOCK,24)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:queue.claim(n,'message','-100123',CLOCK,28),candidates))
    assert results.count(True)==1


def test_schedule_has_28_slots_and_no_overnight_catchup():
    schedule=DirectSchedule(9,23,28,24)
    assert len(schedule.slots())==28 and schedule.slots()[-1]==(22,30)
    assert schedule.next_at(CLOCK)==CLOCK.replace(minute=30)
    assert schedule.next_at(CLOCK.replace(hour=23))==CLOCK+timedelta(days=1)


@pytest.mark.parametrize(('key','value'), [('TECH_TG_START_HOUR','23'),('TECH_TG_END_HOUR','25'),
    ('TECH_TG_DAILY_LIMIT','49'),('TECH_TG_MAX_AGE_HOURS','0'),('TECH_TG_START_HOUR','oops')])
def test_invalid_schedule_is_rejected(configured,monkeypatch,key,value):
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):DirectSchedule.from_env()


class Response:
    def __init__(self,data):self.data=data
    def raise_for_status(self):pass
    def json(self):return {'ok':True,'result':self.data}


class Session:
    def __init__(self,can_post=True,chat_type='channel'):
        self.can_post=can_post;self.chat_type=chat_type;self.calls=[]
    def post(self,url,**kwargs):
        method=url.rsplit('/',1)[-1];self.calls.append(method)
        if method=='getMe':return Response({'id':123})
        if method=='getChat':return Response({'id':-100123,'type':self.chat_type,'title':'Tech','username':'testtech'})
        if method=='getChatMember':return Response({'status':'administrator','can_post_messages':self.can_post})
        raise AssertionError('A channel check must not send messages')


def test_read_only_channel_check_validates_rights_without_sending():
    session=Session()
    result=TechTelegramPublisher('secret','@testtech',session).check_channel()
    assert result['id']==-100123 and result['can_post']
    assert 'sendMessage' not in session.calls
    with pytest.raises(ValueError):TechTelegramPublisher('secret','@testtech',Session(False)).check_channel()
    with pytest.raises(ValueError):TechTelegramPublisher('secret','@testtech',Session(chat_type='private')).check_channel()


def test_token_is_not_exposed_in_channel_check_errors():
    class Broken:
        def post(self,*args,**kwargs):raise requests.ConnectionError('https://api.telegram.org/botSECRET/getChat')
    with pytest.raises(ValueError) as error:TechTelegramPublisher('SECRET','@testtech',Broken()).check_channel()
    assert 'SECRET' not in str(error.value)


def test_same_channel_is_allowed_for_investment_news(configured,monkeypatch):
    news(configured)
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID','-100123')
    publisher=Publisher()
    publisher.check_channel=lambda:{'id':-100123}
    publisher.resolve_chat=lambda channel:{'id':-100123}
    monkeypatch.setattr('invest_blog.tech_direct.TechTelegramPublisher',lambda *args:publisher)
    result=run(configured,TZ,publish=True,translator=translate,clock=CLOCK)
    assert len(result['sent'])==1
    assert len(publisher.messages)==1


def test_vk_dispatch_never_crossposts_during_direct_mode(configured,monkeypatch):
    from invest_blog import tech_publish_dashboard as module
    monkeypatch.setenv('VK_AUTO_PUBLISH','true')
    monkeypatch.setattr(module,'publish_vk',lambda *a,**k:{'published':[]})
    monkeypatch.setattr(module,'publish_tg',lambda *a,**k:pytest.fail('Direct mode must not also crosspost VK'))
    assert module.dispatch(configured,TZ)['telegram'] is None


def test_dashboard_reports_separate_telegram_schedule(configured):
    from invest_blog.tech_publish_dashboard import overview
    status=overview(configured,TZ)
    assert status['next_at'] is None
    assert status['telegram_next_at'] is not None
    assert status['telegram_schedule']['daily_limit']==28
    assert status['telegram_last_run'] is None


def test_missing_model_records_a_failed_run(configured,monkeypatch):
    news(configured)
    def missing():raise RuntimeError('Model not installed')
    monkeypatch.setattr('invest_blog.tech_direct.LocalTranslator',missing)
    result=run(configured,TZ,publish=True,publisher=Publisher(),clock=CLOCK)
    assert result['sent']==[] and result['errors']
    assert DirectQueue(configured).last_run()['status']=='error'


def test_standalone_cycle_refreshes_rss_before_publishing(configured,monkeypatch):
    calls=[]
    monkeypatch.setattr('invest_blog.tech_news.TechNewsCollector.scan',lambda self:calls.append('rss'))
    monkeypatch.setattr('invest_blog.tech_direct.run',lambda *a,**k:calls.append('send') or {'sent':[],'note':'ok'})
    from invest_blog.tech_direct import publish_cycle
    assert publish_cycle(configured,TZ)['note']=='ok'
    assert calls==['rss','send']


def test_standalone_cycle_records_generic_errors_without_tokens(configured,monkeypatch):
    monkeypatch.setattr('invest_blog.tech_news.TechNewsCollector.scan',lambda self:None)
    def failure(*a,**k):raise RuntimeError('https://api.telegram.org/botSECRET/sendMessage')
    monkeypatch.setattr('invest_blog.tech_direct.run',failure)
    from invest_blog.tech_direct import publish_cycle
    result=publish_cycle(configured,TZ)
    assert 'SECRET' not in str(result)
    assert DirectQueue(configured).last_run()['status']=='error'


def test_api_requires_csrf_and_explicit_publish_confirmation(configured,monkeypatch):
    from invest_blog.dashboard import create_app
    monkeypatch.setattr('invest_blog.tech_direct.run',lambda *a,**k:{'prepared':[],'sent':[],'note':'test'})
    with TestClient(create_app(configured)) as client:
        assert client.post('/api/tech/publishing/telegram-preview').status_code==403
        assert client.post('/api/tech/publishing/telegram-now',json={'confirm':'PUBLISH'}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        assert client.post('/api/tech/publishing/telegram-preview',headers=headers).status_code==200
        assert client.post('/api/tech/publishing/telegram-now',headers=headers,json={'confirm':'no'}).status_code==422
        assert client.post('/api/tech/publishing/telegram-now',headers=headers,json={'confirm':'PUBLISH'}).status_code==200


@pytest.mark.parametrize('invalid',[False,True,'worker'])
def test_bot_registers_direct_job_and_keeps_investment_jobs(configured,monkeypatch,invalid):
    from invest_blog.bot import InvestBot
    from invest_blog.config import Settings, DEFAULT_TIMES
    if invalid is True:monkeypatch.setenv('TECH_TG_START_HOUR','oops')
    if invalid == 'worker':monkeypatch.setenv('TECH_TG_RUNNER','worker')
    settings=Settings('test',('a',),'test',1,TZ,configured,configured.with_suffix('.json'),False,DEFAULT_TIMES)
    bot=InvestBot(settings)
    class Scheduler:
        def __init__(self):self.jobs={}
        def add_job(self,*args,**kwargs):self.jobs[kwargs['id']]=(args,kwargs)
        def start(self):pass
    scheduler=Scheduler();bot.scheduler=scheduler
    monkeypatch.setattr('invest_blog.bot.asyncio.create_task',lambda coro:coro.close())
    asyncio.run(bot.post_init(object()))
    assert 'publishing' in scheduler.jobs and 'placements' in scheduler.jobs
    assert ('tech-telegram-direct' in scheduler.jobs) == (not invalid)
    if not invalid:
        args,options=scheduler.jobs['tech-telegram-direct']
        assert options['misfire_grace_time']==120 and options['coalesce'] is True
        assert args[1].get_next_fire_time(None,CLOCK)==CLOCK
    else:assert 'tech-rss' in scheduler.jobs
