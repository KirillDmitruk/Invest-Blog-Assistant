"""All trading is fake: private callbacks, one-shot confirmation and checkpoints."""
import json,sqlite3,time
from contextlib import contextmanager
from copy import deepcopy
import pytest
from invest_blog.walt_telegram_protocol import button,verify_button,b64,seal_event,open_event
from invest_blog.telegram_walt import TradeBot
from invest_blog.walt_cloud_state import CloudWaltAgent

OWNER='123456789';KEY='test-only-signing-key'

def event(action,query='query1',message='42'):
    return dict(data=button(action,OWNER,KEY),owner=OWNER,chat=OWNER,message=message,query=query,created=int(time.time()))

def test_encrypted_event_rejects_tampering_other_owner_and_staleness():
    key=b64(b'x'*32);value=event(f'n:123:{int(time.time()+60)}')
    envelope=seal_event(value,key)
    assert open_event(envelope,key,OWNER,KEY)==value
    for candidate in (envelope[:-4]+'AAAA',seal_event({**value,'chat':'999'},key),seal_event({**value,'created':0},key)):
        with pytest.raises(ValueError):open_event(candidate,key,OWNER,KEY)
    with pytest.raises(ValueError):open_event(envelope,key,'999',KEY)

@pytest.mark.parametrize('body',['e:xyz:2000000000','q:1:2:x:2000000000','n:1:0','z:1:2000000000'])
def test_invalid_signed_actions_still_rejected(body):
    with pytest.raises(ValueError):verify_button(button(body,OWNER,KEY),OWNER,KEY)

class FakeAgent:
    def __init__(self,path):self.path=path
    @contextmanager
    def db(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory=sqlite3.Row
            yield db

class FakeService:
    def __init__(self,path):
        self.agent=FakeAgent(path);self.executions=0;self.reads=0
        self.draft={'id':'draft','status':'previewed','preview_hash':'hash','preview_expires':time.time()+65,
            'ai':{'side':'wait'},'proposal':{'side':'long','symbol':'BTC','margin':'2','currency':'USDT',
            'leverage':2,'risk_estimate':'.2','stop':'90','take':'120'}}
        with self.agent.db() as db:
            db.executescript("CREATE TABLE walt_trade_drafts(id TEXT,status TEXT); INSERT INTO walt_trade_drafts VALUES('draft','previewed'); CREATE TABLE walt_agent_secrets(name TEXT);")
    def execute(self,ident,preview_hash,confirm):
        assert (ident,preview_hash,confirm)==('draft','hash','OPEN_REAL_POSITION')
        self.executions+=1;self.draft['status']='uncertain';return deepcopy(self.draft)
    def public(self,ident):return deepcopy(self.draft)
    def reconcile(self,ident):self.reads+=1;return deepcopy(self.draft)

def setup(tmp_path,checkpoint=lambda:None):
    service=FakeService(tmp_path/'fake.db');messages=[]
    def send(text,markup):messages.append((text,markup));return len(messages)+41
    controller=TradeBot(service,OWNER,KEY,send,checkpoint);controller.show(service.draft)
    data=messages[0][1]['inline_keyboard'][0][0]['callback_data']
    return service,controller,messages,data

def test_duplicate_confirmation_executes_once_and_only_reconciles(tmp_path):
    service,controller,messages,data=setup(tmp_path)
    value={**event('n:1:'+str(int(time.time()+60))), 'data':data}
    controller.handle(value)
    assert controller.handle(value)=='duplicate'
    controller.handle({**value,'query':'different-query'})
    assert service.executions==1 and service.reads==1
    assert 'реальные деньги' in messages[0][1]['inline_keyboard'][0][0]['text']

def test_message_binding_and_owner_reject_before_execution(tmp_path):
    service,controller,_,data=setup(tmp_path)
    for change in ({'message':'999'},{'owner':'999'},{'chat':'999'}):
        with pytest.raises(ValueError):controller.handle({**event('n:1:'+str(int(time.time()+60))), 'data':data,**change})
    assert service.executions==0

def test_cancel_consumes_confirmation_without_order(tmp_path):
    service,controller,messages,_=setup(tmp_path)
    data=messages[0][1]['inline_keyboard'][1][0]['callback_data']
    controller.handle({**event('n:1:'+str(int(time.time()+60))),'data':data})
    assert service.executions==0
    with service.agent.db() as db:assert db.execute('SELECT status FROM walt_trade_drafts').fetchone()[0]=='cancelled'

def test_failed_durable_checkpoint_prevents_execution(tmp_path):
    service,controller,_,data=setup(tmp_path)
    def fail():raise ValueError('storage unavailable')
    controller.checkpoint=fail
    with pytest.raises(ValueError):controller.handle({**event('n:1:'+str(int(time.time()+60))),'data':data})
    assert service.executions==0

def test_cloud_agent_never_calls_transport_if_checkpoint_fails():
    agent=object.__new__(CloudWaltAgent)
    class Store:
        def checkpoint(self):raise ValueError('not durable')
    agent.cloud_store=Store()
    with pytest.raises(ValueError,match='not durable'):
        agent.rpc('tools/call',{'name':'perps_execute_operation'},_manual=True)

def test_expired_confirm_never_executes(tmp_path):
    service,controller,_,data=setup(tmp_path)
    with service.agent.db() as db:db.execute('UPDATE walt_tg_buttons SET expires=0')
    with pytest.raises(ValueError):controller.handle({**event('n:1:'+str(int(time.time()+60))),'data':data})
    assert service.executions==0

def test_blocked_card_explains_missing_confirmation_and_has_no_execute_button(tmp_path):
    service,controller,messages,_=setup(tmp_path)
    controller.show({**service.draft,'status':'blocked','block_reason':'Защита не подтверждена.'})
    text,markup=messages[-1]
    assert 'Расчёт заблокирован' in text and 'Подтверждение недоступно' in text
    assert not markup['inline_keyboard'] and service.executions==0
