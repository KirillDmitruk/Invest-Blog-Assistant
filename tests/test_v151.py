"""v1.5.1: discovery sources, persistence, safe drafting, local endpoints."""
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from invest_blog.dashboard import create_app
from invest_blog.placements import (PlacementMonitor, PlacementStore, finam_calendar,
    from_feed, from_tinvest, render_draft)
from invest_blog.storage import DraftStore

DAY=date(2026,10,30)


def bond(day='2026-11-05T00:00:00Z', name='Тест 001Р', isin='RU000000TEST'):
    return {'name':name,'ticker':'TEST','isin':isin,'uid':'uid1','placementDate':day,
            'maturityDate':'2029-10-30T00:00:00Z',
            'nominal':{'units':'1000','nano':0,'currency':'RUB'},
            'placementPrice':{'units':'999','nano':500000000,'currency':'RUB'},
            'couponQuantityPerYear':4,'floatingCouponFlag':True,
            'amortizationFlag':False,'forQualInvestorFlag':False}


def test_mapping_is_conservative():
    item=from_tinvest(bond(),DAY)
    assert item['nominal']==1000
    assert item['placement_price']==999.5
    assert item['coupon_type']=='Плавающий'
    assert item['offer_date'] is None
    assert from_tinvest(bond('2026-10-29T00:00:00Z'),DAY) is None
    assert from_tinvest({**bond(),'placementDate':None},DAY) is None


def test_feed_is_explicit_and_does_not_invent():
    item=from_feed({'id':'a','name':'Новый выпуск','placement_date':'2026-11-01','source_url':'http://bad.site'},DAY)
    assert item['source']=='feed' and item['source_url'] is None
    assert item['coupon_frequency'] is None
    assert from_feed({'name':'без id','placement_date':'2026-11-01'},DAY) is None


def test_finam_parser_detects_calendar():
    html='''<table><tr><th>Выпуск</th><th>Объём, млн.</th><th>Дата</th><th>Дата погашения</th></tr>
      <tr><td><a href="/ibo/test/">Банк Тест 001Р</a></td><td>1000 RUR</td><td>02.11.2026</td><td>01.10.2029</td></tr>
      <tr><td>Старая</td><td>—</td><td>29.10.2026</td><td>—</td></tr></table>
      <table><tr><td>Чужой выпуск</td><td>—</td><td>02.11.2026</td><td>2028</td></tr></table>'''
    rows=finam_calendar(html,DAY)
    assert len(rows)==1
    assert rows[0]['name']=='Банк Тест 001Р'
    assert rows[0]['source_url']=='https://www.finam.ru/ibo/test/'
    assert rows[0]['maturity_date']=='2029-10-01'
    with pytest.raises(ValueError):finam_calendar('captcha',DAY)


def test_dedup_updates_changes_notifications_and_sort(tmp_path):
    store=PlacementStore(tmp_path/'data.sqlite3')
    a=from_tinvest(bond(),DAY)
    b=from_tinvest(bond('2026-11-01T00:00:00Z','Ранний','RU000000EARL'),DAY)
    assert store.save('tinvest',[a,b])['new']==2
    assert [x['name'] for x in store.upcoming(DAY)]==['Ранний','Тест 001Р']
    assert store.save('tinvest',[a,b])['new']==0
    assert store.save('tinvest',[a,b])['changed']==0
    pending=store.pending()
    store.mark_notified([(x['id'],x['version']) for x in pending])
    assert store.pending()==[]
    revised=dict(a,coupon_frequency=12)
    assert store.save('tinvest',[revised,b])['changed']==1
    assert len(store.pending())==1 and not store.pending()[0]['is_new']
    # Old data survive failed source checks.
    store.failure('tinvest','timeout')
    assert len(store.upcoming(DAY))==2
    assert store.status()[0]['ok']==0
    store.save('tinvest',[])
    assert store.upcoming(DAY)==[]


def test_draft_idempotent_and_safe(tmp_path):
    store=PlacementStore(tmp_path/'db.sqlite3')
    drafts=DraftStore(tmp_path/'db.sqlite3')
    store.save('tinvest',[from_tinvest(bond(),DAY)])
    issue=store.upcoming(DAY)[0]['id']
    first=store.create_draft(issue)
    assert store.create_draft(issue)==first
    row=drafts.get(first)
    assert row['kind']=='placement' and row['state']=='new'
    assert 'Тест 001Р' in row['body']
    assert 'уточнить' in row['body'] and len(row['body'])<4000
    assert drafts.delete_from_queue(first,row['revision'])
    second=store.create_draft(issue)
    assert second!=first
    assert store.create_draft(issue)==second


def test_web_placements_read_and_create_with_csrf(tmp_path):
    db=tmp_path/'db.sqlite3'
    store=PlacementStore(db)
    store.save('tinvest',[from_tinvest(bond(),DAY)])
    app=create_app(db,timezone=ZoneInfo('Europe/Moscow'))
    client=TestClient(app)
    resp=client.get('/api/placements')
    assert resp.status_code==200
    issue=store.upcoming(DAY)[0]['id']
    endpoint=f'/api/placements/{issue}/draft'
    assert client.post(endpoint).status_code==403
    token=client.get('/api/editor-token').json()['token']
    assert client.post(endpoint,headers={'X-CSRF-Token':token,'Origin':'http://evil.local'}).status_code==403
    response=client.post(endpoint,headers={'X-CSRF-Token':token})
    assert response.status_code==201
    assert response.json()['kind']=='placement'
    assert response.json()['id']==client.post(endpoint,headers={'X-CSRF-Token':token}).json()['id']
    assert client.get('/api/editor/drafts/'+str(response.json()['id'])).status_code==200


class FakeAPI:
    tz=ZoneInfo('Europe/Moscow')
    def __init__(self,raise_error=False):self.raise_error=raise_error
    def call(self,service,method,payload):
        if self.raise_error:raise RuntimeError('API down')
        assert payload['instrumentStatus']=='INSTRUMENT_STATUS_ALL'
        return {'instruments':[bond()]}
    def coupons(self,*args,**kwargs):return []

class FakeResponse:
    text='''<table><tr><th>Выпуск</th><th>Объём</th><th>Дата</th><th>Дата погашения</th></tr>
    <tr><td>Другая бумага</td><td>500 RUR</td><td>03.11.2026</td><td>05.11.2028</td></tr></table>'''
    def raise_for_status(self):pass
    def json(self):return {'placements':[{'id':'feed-1','name':'Выпуск из JSON','placement_date':'2026-11-06'}]}

class FakeSession:
    def get(self,url,**kwargs):
        assert url.startswith('https://')
        return FakeResponse()


def test_multiple_sources_and_error_isolation(tmp_path):
    store=PlacementStore(tmp_path/'db.sqlite3')
    monitor=PlacementMonitor(store,FakeAPI(),finam_enabled=True,feed_url='https://myfeed.example/api',session=FakeSession())
    result=monitor.scan(DAY)
    assert not result['busy']
    assert {p['source'] for p in store.upcoming(DAY)}=={'tinvest','finam','feed'}
    # Failed T-Invest never wipes its prior records; Finam and feed can still run.
    down=PlacementMonitor(store,FakeAPI(True),finam_enabled=True,session=FakeSession())
    result=down.scan(DAY)
    assert result['sources'][0]['error']=='RuntimeError'
    assert any(x['source']=='tinvest' for x in store.upcoming(DAY))


def test_calendar_failure_preserves_data(tmp_path):
    store=PlacementStore(tmp_path/'db.sqlite3')
    store.save('finam',[{'source':'finam','source_key':'old','placement_date':'2026-11-02','name':'Old'}])
    class BadSession:
        def get(self,*args,**kwargs):raise ConnectionError('offline')
    result=PlacementMonitor(store,FakeAPI(),finam_enabled=True,session=BadSession()).scan(DAY)
    assert result['sources'][1]['error']=='ConnectionError'
    assert any(x['source']=='finam' for x in store.upcoming(DAY))


def test_telegram_paginated_menu(tmp_path):
    pytest.importorskip('apscheduler')
    pytest.importorskip('telegram')
    from invest_blog.bot import InvestBot
    from invest_blog.config import Settings
    db=tmp_path/'db.sqlite3'
    store=PlacementStore(db)
    rows=[]
    for n in range(11):
        b=bond('2026-11-03T00:00:00Z',f'Бумага {n}',f'RU{n:06d}')
        rows.append(from_tinvest(b,DAY))
    store.save('tinvest',rows)
    settings=Settings(invest_token='test',account_ids=('test',),telegram_token='test',
        telegram_chat_id=1,timezone=ZoneInfo('Europe/Moscow'),db_path=db,
        cbr_path=tmp_path/'cbr.json',startup_catchup=False,times={})
    bot=InvestBot(settings)
    text,keyboard=bot.placements_keyboard(0)
    assert '11' in text
    assert len(keyboard.inline_keyboard)==9
    _,next_page=bot.placements_keyboard(1)
    assert len(next_page.inline_keyboard)==4
