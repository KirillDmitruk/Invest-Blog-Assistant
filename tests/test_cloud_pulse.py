"""Private drafts: schedule, encryption, read restrictions, no duplicate sends."""
from copy import deepcopy
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
import base64,json
import pytest
from cryptography.fernet import Fernet,InvalidToken
from invest_blog.cloud_pulse import due,period,send_due,PrivateState,PrivateTelegram,BrokerReadOnly
from invest_blog.model import Draft


@pytest.mark.parametrize('stamp,kinds',[
 ('2026-11-23T09:59:00',[]),('2026-11-23T10:00:00',['plan']),
 ('2026-11-25T10:00:00',['paper']),('2026-11-27T10:00:00',['glossary']),
 ('2026-11-28T10:00:00',['weekly']),('2026-12-01T11:00:00',['monthly']),
 ('2027-02-01T11:00:00',['plan','monthly']),('2026-11-23T13:00:00',[])])
def test_existing_moscow_schedule_is_preserved(stamp,kinds):
    assert due(datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('Europe/Moscow')))==kinds


def test_utc_trigger_is_converted_to_moscow():
    assert due(datetime(2026,11,23,7,tzinfo=timezone.utc))==['plan']
    with pytest.raises(ValueError):due(datetime(2026,11,23,10))


class Store:
    def __init__(self):self.value={'version':1,'deliveries':{},'papers':[],'glossary':[]};self.events=[];self.fail=False
    def load(self):return deepcopy(self.value)
    def save(self,value):
        if self.fail:raise ValueError('unavailable')
        self.value=deepcopy(value);self.events.append(deepcopy(value))


class Generator:
    selected_paper_uid=None
    selected_glossary_topic='Duration'
    def generate(self,kind,day):return Draft(kind,period(kind,day),'PRIVATE PORTFOLIO DATA')


class Sender:
    def __init__(self):self.calls=0;self.fail=False
    def send(self,draft):
        self.calls+=1
        if self.fail:raise ValueError('lost response')


def test_attempt_saved_before_send_and_same_period_is_not_resent():
    s=Store();sender=Sender();factory=lambda _:Generator();day=date(2026,11,27)
    assert send_due(['glossary'],day,s,sender,factory,'12345')=={'glossary':'sent'}
    assert list(s.events[0]['deliveries'].values())[0]['status']=='attempted'
    assert send_due(['glossary'],day,s,sender,factory,'12345')=={'glossary':'already_attempted'}
    assert sender.calls==1 and s.value['glossary']==['Duration']
    assert 'PRIVATE PORTFOLIO DATA' not in json.dumps(s.value)


def test_ambiguous_send_stays_claimed_and_storage_failure_never_sends():
    s=Store();sender=Sender();sender.fail=True;day=date(2026,11,27)
    with pytest.raises(ValueError):send_due(['glossary'],day,s,sender,lambda _:Generator(),'12345')
    sender.fail=False;send_due(['glossary'],day,s,sender,lambda _:Generator(),'12345');assert sender.calls==1
    s=Store();s.fail=True
    with pytest.raises(ValueError):send_due(['glossary'],day,s,sender,lambda _:Generator(),'12345')
    assert sender.calls==1


def test_broker_write_method_never_reaches_transport():
    api=BrokerReadOnly('SECRET',ZoneInfo('Europe/Moscow'))
    assert str(api.session.verify).endswith('tbank-ca.pem')
    with pytest.raises(ValueError):api.call('OrdersService','PostOrder',{})


def test_state_is_encrypted_before_github_receives_it(monkeypatch):
    import invest_blog.cloud_pulse as module
    captured=[];key=Fernet.generate_key();state=Store().value;state['papers']=['PRIVATE_UID']
    def call(session,method,url,**kw):captured.append(kw['json']);return {'content':{'sha':'next'}}
    monkeypatch.setattr(module,'json_call',call)
    obj=PrivateState('owner/repo','TOKEN','a'*40,key)
    obj.save(state)
    encrypted=base64.b64decode(captured[0]['content'])
    assert b'PRIVATE_UID' not in encrypted and 'PRIVATE_UID' not in json.dumps(captured)
    assert json.loads(Fernet(key).decrypt(encrypted))['papers']==['PRIVATE_UID']
    with pytest.raises(InvalidToken):Fernet(Fernet.generate_key()).decrypt(encrypted)


def test_retention_preserves_recent_glossary_marker_not_alphabetical_rubrics(monkeypatch):
    import invest_blog.cloud_pulse as module
    key=Fernet.generate_key();saved=[];state=Store().value
    state['deliveries']={f'weekly:old:{i}':{'period':'2025-01-01','status':'sent'} for i in range(105)}
    state['deliveries']['glossary:new']={'period':'2026-11-23','created_at':'2026-11-27T07:00:00+00:00','status':'attempted'}
    def call(session,method,url,**kw):saved.append(kw['json']);return {'content':{'sha':'new'}}
    monkeypatch.setattr(module,'json_call',call)
    PrivateState('owner/repo','TOKEN','a'*40,key).save(state)
    result=json.loads(Fernet(key).decrypt(base64.b64decode(saved[0]['content'])))
    assert len(result['deliveries'])==100 and 'glossary:new' in result['deliveries']


def test_long_post_goes_as_private_document_with_no_truncation(monkeypatch):
    import invest_blog.cloud_pulse as module
    calls=[]
    def call(session,method,url,**kw):
        calls.append((url,kw))
        if url.endswith('getMe'):return {'ok':True,'result':{'id':99}}
        if url.endswith('getChat'):return {'ok':True,'result':{'id':12345,'type':'private'}}
        return {'ok':True,'result':{'message_id':1}}
    monkeypatch.setattr(module,'json_call',call)
    sender=PrivateTelegram('SECRET','12345');body='Данные '*1000
    sender.send(Draft('paper','2026-11-23',body))
    assert calls[-1][0].endswith('sendDocument')
    assert calls[-1][1]['files']['document'][1].decode()==body


@pytest.mark.parametrize('new',[True,False])
def test_missing_state_is_initialized_only_on_new_branch(monkeypatch,new):
    import invest_blog.cloud_pulse as module
    calls=[]
    class R:
        def __init__(self,status):self.status_code=status
    class Session:
        def get(self,url,**kw):return R(404 if '/contents/' in url or new else 200)
    def call(session,method,url,**kw):calls.append((method,kw));return {'content':{'sha':'new'}}
    monkeypatch.setattr(module,'json_call',call)
    obj=PrivateState('owner/repo','TOKEN','a'*40,Fernet.generate_key(),Session())
    if new:
        assert obj.load()['deliveries']=={}
        assert [m for m,_ in calls]==['POST','PUT']
        assert obj.sha=='new'
    else:
        with pytest.raises(ValueError):obj.load()
        assert calls==[]
