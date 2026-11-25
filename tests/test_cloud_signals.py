"""All provider calls are fake. No wallet methods or credentials are used."""
from copy import deepcopy
import json,time
import pytest
from invest_blog.cloud_signals import scan,review,report,deliver,GithubLedger


def packet():
    return {'created_at':'2026-11-21T16:17:00+00:00','checked':1,'total':2,'errors':['NEW'],
        'ai':{'status':'not_configured'},'scope':'Public Lighter, not Walt.',
        'news':{'items':[],'sources':[]},'rows':[{'market_id':0,'symbol':'BTC','side':'long',
        'metrics':{'close':100,'change_24h_pct':2,'change_7d_pct':4,'rsi14':55,'atr_pct':1},'volume_24h_quote':2e6,'cutoff':1}]}


class Response:
    status_code=200
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield json.dumps(self.value).encode()


class Session:
    def __init__(self,model=None):self.calls=[];self.model=model;self.chat_type='private';self.self_chat=False;self.fail=False
    def request(self,method,url,**kw):
        self.calls.append((url,kw))
        if 'cloudflare.com' in url:return Response({'success':True,'result':{'response':json.dumps(self.model)}})
        if url.endswith('getMe'):return Response({'ok':True,'result':{'id':99999}})
        if url.endswith('getChat'):return Response({'ok':True,'result':{'id':99999 if self.self_chat else 12345,'type':self.chat_type}})
        if url.endswith('sendMessage'):
            if self.fail:raise RuntimeError('SECRET_TOKEN '+url)
            return Response({'ok':True,'result':{'message_id':1}})
        raise AssertionError(url)


class Ledger:
    def __init__(self):self.state={};self.events=[];self.fail=False
    def load(self):return deepcopy(self.state)
    def save(self,value):
        if self.fail:raise ValueError('state unavailable')
        self.state=deepcopy(value);self.events.append(deepcopy(value))


def test_unconfigured_model_is_never_called_or_claimed():
    p=packet();s=Session();review(p,'','',s)
    assert not s.calls and 'не подключён' in report(p)
    assert 'без проверки ии' in report(p).lower()


@pytest.mark.parametrize('bad',[
    [{'market_id':99,'accept':True,'reason':'checks_pass'}],
    [{'market_id':0,'accept':True,'reason':'news_risk'}],
    [{'market_id':0,'accept':'yes','reason':'checks_pass'}],[],
    [{'market_id':0,'accept':True,'reason':'checks_pass'}]*2])
def test_model_output_cannot_invent_or_omit_candidates(bad):
    p=packet();review(p,'a'*32,'SECRET',Session({'reviews':bad}))
    assert p['ai']['status']=='unavailable' and 'ai_review' not in p['rows'][0]


def test_model_can_only_accept_existing_direction():
    p=packet();review(p,'a'*32,'SECRET',Session({'reviews':[{'market_id':0,'accept':True,'reason':'checks_pass'}]}))
    assert p['ai']['status']=='ok' and p['rows'][0]['side']=='long'
    assert 'ИИ: рассмотреть' in report(p)


def test_intent_is_persisted_before_send_and_prevents_duplicate():
    p=packet();s=Session();ledger=Ledger()
    assert deliver(p,'SECRET','12345',ledger,s)=='sent'
    assert list(ledger.events[0].values())[0]['status']=='attempted'
    assert '12345' not in json.dumps(ledger.state) and 'SECRET' not in json.dumps(ledger.state)
    assert deliver(p,'SECRET','12345',ledger,s)=='already_attempted'
    assert sum(url.endswith('sendMessage') for url,_ in s.calls)==1


def test_lost_send_response_never_retries_or_leaks_secret():
    p=packet();s=Session();s.fail=True;ledger=Ledger()
    with pytest.raises(ValueError) as caught:deliver(p,'SECRET','12345',ledger,s)
    assert 'SECRET' not in str(caught.value)
    s.fail=False
    assert deliver(p,'SECRET','12345',ledger,s)=='already_attempted'
    assert sum(url.endswith('sendMessage') for url,_ in s.calls)==1


def test_missing_state_storage_blocks_sending():
    ledger=Ledger();ledger.fail=True;s=Session()
    with pytest.raises(ValueError):deliver(packet(),'SECRET','12345',ledger,s)
    assert not any(url.endswith('sendMessage') for url,_ in s.calls)


@pytest.mark.parametrize('kind',['channel','self','negative'])
def test_only_private_human_chat_can_receive_report(kind):
    s=Session();s.chat_type='channel' if kind=='channel' else 'private';s.self_chat=kind=='self'
    with pytest.raises(ValueError):deliver(packet(),'SECRET','-12345' if kind=='negative' else '12345',Ledger(),s)
    assert not any(url.endswith('sendMessage') for url,_ in s.calls)


def test_scan_uses_entire_catalog_including_zero_and_does_not_claim_wallet_data(tmp_path,monkeypatch):
    import invest_blog.cloud_signals as module
    calls=[];cutoff=int(time.time())//3600*3600
    class History:
        def __init__(self,*a):pass
        def get(self,path):
            assert path=='orderBooks'
            return {'order_books':[{'market_id':i,'symbol':'BTC' if i==0 else 'NEW','market_type':'perp','status':'active','is_frozen':False,'multiplier':'1'} for i in range(2)]}
        def load(self,c):
            calls.append(c['contract_id'])
            if c['contract_id']==1:raise ValueError('too little history')
            return {'cutoff':cutoff,'bars':[{'o':100,'h':101,'l':99,'c':100,'V':100000,'t':cutoff-(240-i)*3600} for i in range(240)]}
    monkeypatch.setattr(module,'LighterHistory',History)
    monkeypatch.setattr(module,'headlines',lambda *a:{'sources':[],'items':[]})
    p=scan(None,Session());assert calls==[0,1] and p['checked']==1 and p['total']==2 and p['errors']==['NEW']
    assert 'не подтверждённый каталог Walt' in p['scope']
