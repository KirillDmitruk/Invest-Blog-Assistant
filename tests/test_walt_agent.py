"""Local inference, read-only OAuth/MCP, credentials and execution boundaries."""
import base64
import json
import logging
import os
import time
from urllib.parse import parse_qs,urlsplit

import pytest
from fastapi.testclient import TestClient
from invest_blog.walt_agent import WaltAgent,protect,callback_url,safe_read_tool,compact_read_result,MCP_URL,OAUTH,ISSUER
from invest_blog.walt_agent_routes import CallbackLogFilter


def crypto(data,decrypt=False):return base64.b64decode(data) if decrypt else base64.b64encode(data)


class Response:
    def __init__(self,data,status=200,headers=None):self.data=data;self.status_code=status;self.headers=headers or {'content-type':'application/json'}
    def json(self):return self.data
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError('SECRET')
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def iter_content(self,size):yield json.dumps(self.data).encode()


READ={'name':'perps_latest_rate','description':'Read market','inputSchema':{'type':'object','properties':{},'additionalProperties':False},'annotations':{'readOnlyHint':True}}
WRITE={'name':'open_position','description':'Real trade','inputSchema':{'type':'object'}}


class Transport:
    def __init__(self):self.calls=[];self.tools=[READ,WRITE]
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs))
        if url.endswith('oauth-authorization-server/bot-relay/public-api'):
            return Response({'issuer':ISSUER,'authorization_endpoint':OAUTH+'authorize','token_endpoint':OAUTH+'token','registration_endpoint':OAUTH+'register','code_challenge_methods_supported':['S256']})
        if url.endswith('register'):return Response({'client_id':'client-test'})
        if url.endswith('token'):return Response({'access_token':'SECRET_ACCESS','refresh_token':'SECRET_REFRESH','scope':'perps.readonly','expires_in':3600})
        raise AssertionError(url)
    def post(self,url,**kwargs):
        assert url==MCP_URL and kwargs['allow_redirects'] is False
        message=kwargs['json'];self.calls.append(('RPC',message['method'],message.get('params')))
        method=message['method']
        if method=='initialize':result={'protocolVersion':'2025-06-18'}
        elif method=='notifications/initialized':return Response({},202)
        elif method=='tools/list':result={'tools':self.tools}
        elif method=='tools/call':
            assert message['params']['name']=='perps_latest_rate'
            result={'content':[{'type':'text','text':'{"market":"BTCUSDT","price":"50000","as_of":"test"}'}]}
        else:raise AssertionError(method)
        return Response({'jsonrpc':'2.0','id':message['id'],'result':result})


@pytest.fixture
def agent(tmp_path):
    obj=WaltAgent(tmp_path/'lab.db',session=Transport(),crypto=crypto)
    return obj


def authorized(agent):
    agent.save_tokens({'access_token':'SECRET_ACCESS','refresh_token':'SECRET_REFRESH','expires_in':3600,'scope':'perps.readonly'})
    agent.put_secret('client',{'client_id':'client-test'})


def ready(agent):
    authorized(agent);agent.discover();state=agent.state();agent.configure(state['revision'],'qwen3:4b',['perps_latest_rate'])


def test_initial_page_has_no_fake_connection_or_trades(agent):
    result=agent.overview()
    assert not result['connected'] and not result['history'] and not result['real_orders_available']
    assert result['state']['allowed_tools']==[] and result['scope']=='perps.readonly'


def test_oauth_pkce_readonly_and_encrypted_pending(agent):
    result=agent.start_auth('http://127.0.0.1:8000/')
    query=parse_qs(urlsplit(result['url']).query)
    assert query['scope']==['perps.readonly'] and query['code_challenge_method']==['S256']
    pending=agent.secret('pending')
    assert pending['verifier'] not in result['url']
    with agent.db() as db:raw=bytes(db.execute("SELECT value FROM walt_agent_secrets WHERE name='pending'").fetchone()[0])
    assert pending['verifier'].encode() not in raw
    agent.finish_auth('CODE',pending['state'])
    assert agent.overview()['connected']
    assert 'SECRET_ACCESS' not in json.dumps(agent.overview())
    with pytest.raises(ValueError):agent.finish_auth('CODE',pending['state'])


def test_wrong_or_expired_oauth_state_never_exchanges_tokens(agent):
    agent.start_auth('http://localhost:8000')
    with pytest.raises(ValueError):agent.finish_auth('CODE','wrong')
    assert not any(url.endswith('/token') for _,url,_ in agent.session.calls)
    pending=agent.secret('pending');pending['created']=0;agent.put_secret('pending',pending)
    with pytest.raises(ValueError):agent.finish_auth('CODE',pending['state'])


@pytest.mark.parametrize('url',['https://example.org','http://evil.test','http://127.0.0.1@evil.test','http://localhost:8000/path'])
def test_callback_origin_is_local_only(url):
    with pytest.raises(ValueError):callback_url(url)


@pytest.mark.parametrize('scope',['perps.trade','perps.readonly perps.trade','anything'])
def test_trading_oauth_scope_is_rejected(agent,scope):
    with pytest.raises(ValueError):agent.save_tokens({'access_token':'SECRET','scope':scope})
    assert not agent.overview()['connected']


def test_disconnect_during_token_exchange_does_not_restore_access(agent):
    agent.start_auth('http://localhost:8000');pending=agent.secret('pending')
    original=agent.http
    def http(method,url,**kwargs):
        result=original(method,url,**kwargs)
        if url.endswith('/token'):agent.disconnect()
        return result
    agent.http=http
    with pytest.raises(ValueError):agent.finish_auth('CODE',pending['state'])
    assert not agent.overview()['connected']


def test_discovery_does_not_grant_tool_permissions_and_changed_schema_revokes(agent):
    authorized(agent);tools=agent.discover()
    assert len(tools)==2 and not tools[1]['read_candidate'] and agent.state()['allowed_tools']==[]
    state=agent.state();agent.configure(state['revision'],'qwen3:4b',['perps_latest_rate'])
    agent.session.tools=[{**READ,'description':'Changed'}]
    agent.discover();assert agent.state()['allowed_tools']==[]


def test_write_tools_and_cloud_models_are_blocked(agent):
    ready(agent);state=agent.state()
    with pytest.raises(ValueError):agent.configure(state['revision'],'qwen3:4b',['open_position'])
    with pytest.raises(ValueError):agent.configure(state['revision'],'qwen3:cloud',['perps_latest_rate'])
    assert not safe_read_tool(WRITE)
    for name in ('perps_prepare_order_place','perps_execute_operation','perps_readiness','get_unreviewed_command'):
        assert not safe_read_tool({'name':name,'annotations':{'readOnlyHint':True}})
        with pytest.raises(ValueError):agent.rpc('tools/call',{'name':name,'arguments':{}})


def test_local_model_reads_actual_tool_result_before_answer(agent):
    ready(agent);calls=[]
    def local(path,body=None):
        calls.append(body)
        if len(calls)==1:return {'message':{'content':json.dumps({'action':'read','tool':'perps_latest_rate','arguments':{},'answer':''})}}
        assert '50000' in body['messages'][-1]['content']
        return {'message':{'content':json.dumps({'action':'answer','tool':'','arguments':{},'answer':'Данные получены. Оснований для входа недостаточно.'})}}
    agent.local_request=local
    result=agent.analyze('Проверь рынок без исполнения сделок')
    assert result['reads'][0]['tool']=='perps_latest_rate' and len(agent.overview()['history'])==1
    assert calls[0]['model']=='qwen3:4b'
    assert all(t['function']['name']=='perps_latest_rate' for t in json.loads(calls[0]['messages'][2]['content'].split(': ',1)[1]))


@pytest.mark.parametrize('kind',['hallucinated','write','no_data'])
def test_model_cannot_invent_tool_or_answer_without_data(agent,kind):
    ready(agent)
    def local(*args):
        if kind=='no_data':return {'message':{'content':'I bought BTC'}}
        return {'message':{'content':json.dumps({'action':'read','tool':'open_position' if kind=='write' else 'unknown','arguments':{},'answer':''})}}
    agent.local_request=local
    with pytest.raises(ValueError):agent.analyze('Проанализируй данные Walt')
    assert not agent.overview()['history']
    assert not any(m=='RPC' and method=='tools/call' for m,method,_ in agent.session.calls)


def test_disconnect_during_model_step_blocks_tool_call(agent):
    ready(agent)
    def local(*args):
        agent.disconnect()
        return {'message':{'content':json.dumps({'action':'read','tool':'perps_latest_rate','arguments':{},'answer':''})}}
    agent.local_request=local
    with pytest.raises(ValueError):agent.analyze('Проверь текущий рынок')


def test_callback_log_never_includes_oauth_code():
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s %s %s %s %s',('client','GET','/api/walt-agent/callback?code=SECRET&state=STATE','1.1',200),None)
    assert CallbackLogFilter().filter(record)
    assert 'SECRET' not in record.getMessage()


def test_real_catalog_shape_is_compacted_without_inventing_prices():
    rows=[{'contract_id':i,'base_asset':f'ASSET{i}','is_active':True,'volume_24h_usd':str(i*100),'quote_asset':'USDC'} for i in range(100)]
    result=compact_read_result('perps_list_contracts',{'content':[{'type':'text','text':json.dumps({'contracts':rows})}]})
    data=json.loads(result['content'][0]['text'])
    assert data['total_contracts']==100 and data['shown']==12
    assert data['contracts'][0]['contract_id']==99
    assert all('price' not in r for r in data['contracts'])


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI')
def test_windows_dpapi_roundtrip():
    value=b'secret credential';encrypted=protect(value)
    assert value not in encrypted and protect(encrypted,True)==value


def test_agent_api_requires_csrf_and_has_no_trade_mode(tmp_path):
    from invest_blog.dashboard import create_app
    with TestClient(create_app(tmp_path/'app.db')) as client:
        assert client.post('/api/walt-agent/auth/start').status_code==403
        assert client.post('/api/walt-agent/tools').status_code==403
        assert client.post('/api/walt-agent/disconnect').status_code==403
        assert client.post('/api/walt-agent/analyze',json={'prompt':'Анализ рынка'}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        state=client.get('/api/walt-agent/overview').json()['state']
        body={'revision':state['revision'],'model':state['model'],'allowed_tools':[]}
        assert client.put('/api/walt-agent/settings',headers=headers,json={**body,'live':True}).status_code==422
        assert client.post('/api/walt-agent/trade',headers=headers,json={}).status_code==404
        assert client.get('/static/walt-agent.js').status_code==200


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI')
def test_callback_refresh_does_not_report_failure_after_saved_login(tmp_path):
    from invest_blog.dashboard import create_app
    path=tmp_path/'app.db'
    real=WaltAgent(path);real.save_tokens({'access_token':'TEST','scope':'perps.readonly'})
    with TestClient(create_app(path)) as client:
        response=client.get('/api/walt-agent/callback')
        assert response.status_code==200 and 'Вернуться к агенту Walt' in response.text
        assert 'TEST' not in response.text
