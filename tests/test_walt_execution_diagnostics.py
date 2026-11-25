"""Execute errors and recovery: every broker write is simulated."""
import json,time
import pytest,requests
from test_walt_trading import service,quote,wrapped
from test_walt_agent import Response,authorized
from invest_blog.walt_agent import WaltAgent,TRADE_SCOPE
from invest_blog.walt_errors import WaltRPCError
from invest_blog.walt_trading import unpack_execution


def test_scalar_order_id_response_is_not_confused_with_missing_response():
    assert unpack_execution(wrapped('123456'))=={'order_id':'123456'}
    assert unpack_execution(wrapped(123456))=={'order_id':'123456'}
    with pytest.raises(WaltRPCError):unpack_execution(wrapped(True))


def test_tool_error_reports_only_documented_code_never_server_secrets():
    with pytest.raises(WaltRPCError) as caught:
        unpack_execution({'isError':True,'content':[{'type':'text','text':'agent.trading_disabled SECRET_ACCESS confirmationToken=SECRET'}]})
    assert caught.value.diagnostic['code']=='agent.trading_disabled'
    assert 'SECRET' not in str(caught.value)


@pytest.mark.parametrize('kind',['timeout','connection','rpc','http'])
def test_transport_preserves_safe_reason_without_retry(tmp_path,kind):
    from test_walt_agent import Transport,crypto
    transport=Transport();agent=WaltAgent(tmp_path/'rpc.db',session=transport,crypto=crypto)
    agent.save_tokens({'access_token':'SECRET_ACCESS','scope':TRADE_SCOPE},expected_scope=TRADE_SCOPE)
    calls=[]
    def post(url,**kwargs):
        calls.append(kwargs)
        if kind=='timeout':raise requests.Timeout('SECRET_ACCESS')
        if kind=='connection':raise requests.ConnectionError('SECRET_ACCESS')
        if kind=='http':return Response({},403)
        return Response({'id':kwargs['json']['id'],'error':{'code':-32000,'message':'agent.trading_disabled SECRET_ACCESS'}})
    transport.post=post
    with pytest.raises(WaltRPCError) as caught:
        agent.rpc('tools/call',{'name':'perps_execute_operation','arguments':{'confirmationToken':'SECRET'}},_manual=True)
    assert caught.value.diagnostic['kind']==kind and len(calls)==1
    assert 'SECRET' not in str(caught.value)


def simulate_uncertain(s):
    d=quote(s);p=s.prepare(d['id']);s.agent.fail=True
    r=s.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert r['status']=='uncertain'
    return d,p,r


def test_lost_response_has_phase_and_cannot_send_twice(service):
    d,p,r=simulate_uncertain(service)
    assert r['execution_issue']['phase']=='send'
    assert 'SECRET' not in json.dumps(r)
    service.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert service.agent.executions==1
    r=service.reconcile(d['id'])
    assert r['status']=='uncertain' and r['orders_checked']['orders']==[]
    assert 'не найдено' in r['alert']


def test_verify_failure_keeps_received_execution_result(service):
    original=service.agent.rpc
    def rpc(method,params=None,*args,**kw):
        if params['name']=='perps_get_order':raise WaltRPCError('timeout')
        return original(method,params,*args,**kw)
    service.agent.rpc=rpc
    d=quote(service);p=service.prepare(d['id']);r=service.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert r['execution_issue']['phase']=='verify'
    assert r['execution']['result']['order_id']=='test-order'
    assert service.agent.executions==1


def age(s,ident):
    _,d=s.row(ident);d['created']=time.time()-300
    with s.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(d),ident))


def test_manual_absence_resolution_rechecks_and_invalidates_old_token(service):
    d,p,r=simulate_uncertain(service)
    with pytest.raises(ValueError):service.resolve_absent(d['id'],'')
    with pytest.raises(ValueError,match='две минуты'):service.resolve_absent(d['id'],'CONFIRM_NO_POSITION_OR_ORDER')
    age(service,d['id'])
    r=service.resolve_absent(d['id'],'CONFIRM_NO_POSITION_OR_ORDER')
    assert r['status']=='resolved_absent' and r['resolution']
    assert service.agent.secret('trade:'+d['id']) is None
    with pytest.raises(ValueError):service.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    with pytest.raises(ValueError):service.prepare(d['id'])
    assert service.agent.executions==1


def test_pending_order_prevents_manual_absence_resolution(service):
    d,p,r=simulate_uncertain(service);age(service,d['id'])
    service.agent.orders=[{'id':'pending','status':'pending','contract_id':0}]
    with pytest.raises(ValueError,match='Есть позиция'):service.resolve_absent(d['id'],'CONFIRM_NO_POSITION_OR_ORDER')
    assert service.public(d['id'])['status']=='uncertain' and service.agent.executions==1


def test_incomplete_order_history_prevents_resolution(service):
    d,p,r=simulate_uncertain(service);age(service,d['id']);original=service.agent.rpc
    def rpc(method,params=None,*args,**kw):
        if params['name']=='perps_list_orders':return wrapped({'orders':[],'next_cursor':'more'}),'session'
        return original(method,params,*args,**kw)
    service.agent.rpc=rpc
    with pytest.raises(ValueError):service.resolve_absent(d['id'],'CONFIRM_NO_POSITION_OR_ORDER')
    assert service.agent.executions==1
