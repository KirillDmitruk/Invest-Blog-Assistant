from datetime import datetime,timezone,date,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
import pytest
from invest_blog.pulse_editor import EditorialPostGenerator,write_prose
from invest_blog.model import Instrument

def prose(facts,news):
    assert not any(k in facts for k in ['quantity','account','portfolio','balance'])
    return {'intro':'Разберём условия этой бумаги простыми словами.',
        'why_watch':['Условия выпуска помогают оценить будущие выплаты.','Срок погашения важен для личного плана.'],
        'risks':['Цена может меняться вместе с рыночными ставками.','Важно проверять платёжеспособность эмитента.'],
        'watch_next':'Следить за сообщениями эмитента и графиком выплат.',
        'takeaway':'Сравнивать условия и риски, а не только цену.'}

class Api:
    tz=ZoneInfo('Europe/Moscow')
    def portfolio(self,account):return {'totalAmountPortfolio':{'units':'1000','currency':'RUB'},'positions':[
        {'instrumentUid':x,'instrumentType':'share','quantity':{'units':'1'}} for x in ['a','b','c']]}
    def instrument(self,uid='',figi=''):return Instrument(uid,uid,'Бумага '+uid,'','SBER' if uid=='a' else uid.upper(),'share','RUB',{})
    def call(self,service,method,payload):
        if method=='GetLastPrices':return {'lastPrices':[{'instrumentUid':payload['instrumentId'][0],'time':datetime.now(timezone.utc).isoformat(),'price':{'units':'100','nano':0}}]}
        if method=='GetTradingStatus':return {'tradingStatus':'SECURITY_TRADING_STATUS_NORMAL_TRADING'}
        if method=='FindInstrument':return {'instruments':[]}
        raise AssertionError('Unexpected method')

def test_rotation_excludes_history_and_initial_sber_without_private_model_data():
    g=EditorialPostGenerator(Api(),('PRIVATE_ACCOUNT',),Path('unused'),recent_papers=('b',),avoid_tickers=('SBER',),news_loader=lambda *a:[],writer=prose)
    draft=g.paper(date.today())
    assert g.selected_paper_uid=='c' and 'Сбер' not in draft.body
    assert 'PRIVATE_ACCOUNT' not in draft.body and '[уточнить' not in draft.body
    assert g.editorial_status=='ai_verified' and '💰' in draft.body and '⚠️' in draft.body

def test_unavailable_ai_yields_honest_fact_card():
    def fail(*args):raise ValueError('unavailable')
    g=EditorialPostGenerator(Api(),('private',),Path('unused'),news_loader=lambda *a:[],writer=fail)
    draft=g.paper(date.today());assert 'ИИ сейчас недоступен' in draft.body and '100,00 ₽' in draft.body

def test_exhausted_pool_does_not_repeat():
    g=EditorialPostGenerator(Api(),('private',),Path('unused'),recent_papers=('a','b','c'),news_loader=lambda *a:[],writer=prose)
    with pytest.raises(ValueError):g.paper(date.today())

def test_stale_price_is_not_called_current():
    class Stale(Api):
        def call(self,s,m,p):
            value=super().call(s,m,p)
            if m=='GetLastPrices':value['lastPrices'][0]['time']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
            return value
    g=EditorialPostGenerator(Stale(),('private',),Path('unused'),news_loader=lambda *a:[],writer=prose)
    with pytest.raises(ValueError):g.paper(date.today())

def test_ai_cannot_inject_unprovided_numbers(monkeypatch):
    import json
    import invest_blog.pulse_editor as module
    monkeypatch.setenv('CF_ACCOUNT_ID','account');monkeypatch.setenv('CF_AI_TOKEN','fake')
    value=prose({},[]);value['takeaway']='Доходность будет 99 процентов.'
    monkeypatch.setattr(module,'json_call',lambda *a,**k:{'result':{'response':json.dumps(value)}})
    with pytest.raises(ValueError):write_prose({'name':'Public paper'},[])


def test_bond_quote_is_percent_and_only_future_coupon_is_displayed():
    class Bonds(Api):
        def instrument(self,uid='',figi=''):return Instrument(uid,uid,'Тестовый выпуск','RU000TEST','BOND','bond','RUB',{})
        def bond(self,uid):return {'nominal':{'units':'1000','currency':'RUB'},'maturityDate':'2028-12-01T00:00:00Z','floatingCouponFlag':False,'amortizationFlag':True}
        def coupons(self,uid,start,end):return [{'couponDate':(start-timedelta(days=1)).isoformat()+'T00:00:00Z','payOneBond':{'units':'10','currency':'RUB'}},
            {'couponDate':(start+timedelta(days=7)).isoformat()+'T00:00:00Z','payOneBond':{'units':'20','currency':'RUB'}}]
        def call(self,s,m,p):
            v=super().call(s,m,p)
            if m=='GetLastPrices':v['lastPrices'][0]['price']={'units':'99','nano':500000000}
            return v
    g=EditorialPostGenerator(Bonds(),('private',),Path('unused'),news_loader=lambda *a:[],writer=prose)
    body=g.paper(date.today()).body
    assert '99,50% от номинала' in body and '1 000,00 ₽' in body and '20,00 ₽ на бумагу' in body
    assert '10,00 ₽ на бумагу' not in body and 'Возврат номинала частями: да' in body
