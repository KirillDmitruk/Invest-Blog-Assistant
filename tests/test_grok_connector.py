"""Connector transport, user-scoped packets and risk enforcement without external services."""
from datetime import timedelta
import json

import pytest
from fastapi.testclient import TestClient

from invest_blog.trading_connector import AnalysisDesk
from invest_blog.grok_mcp import create_connector
from test_trading_lab import Market, CLOCK, contract


@pytest.fixture
def desk(tmp_path):
    desk = AnalysisDesk(tmp_path/'lab.db')
    desk.lab.watch(contract())
    state = desk.lab.overview()['state']
    desk.lab.configure({'budget': '10000', 'enabled': True, 'strategy': 'connector', 'risk': state['risk']}, state['config_revision'])
    return desk


def decision(packet, **changes):
    return {'analysis_id': packet['id'], 'uid': 'a', 'action': 'long', 'confidence': .8,
            'reason': 'Тестовое предложение на основании признаков.', **changes}


def test_preparation_contains_only_paper_and_market_data(desk,monkeypatch):
    monkeypatch.setenv('TINVEST_TOKEN','BROKER_SECRET')
    packet=desk.prepare(Market(),clock=CLOCK)
    assert packet['mode']=='paper' and len(packet['candidates'])==1
    assert packet['paper_account']['budget']=='10000'
    assert 'BROKER_SECRET' not in json.dumps(packet)
    assert desk.read_packet(CLOCK)['id']==packet['id']


def test_new_packet_supersedes_old_and_expiry_is_enforced(desk):
    first=desk.prepare(Market(),clock=CLOCK)
    second=desk.prepare(Market(),clock=CLOCK+timedelta(seconds=1))
    with pytest.raises(ValueError):desk.submit(decision(first),Market(),CLOCK)
    with pytest.raises(ValueError):desk.read_packet(CLOCK+timedelta(minutes=11))
    assert desk.read_packet(CLOCK+timedelta(minutes=1))['id']==second['id']


def test_decision_executes_once_with_current_price_and_fee(desk):
    market=Market();packet=desk.prepare(market,clock=CLOCK)
    market.price='137'
    first=desk.submit(decision(packet),market,CLOCK)
    assert first['account']['equity']=='9997.00'
    assert desk.lab.overview()['state']['positions']['a']['entry']=='138'
    assert desk.submit(decision(packet),market,CLOCK)==first
    assert len(desk.lab.overview()['trades'])==1


def test_pause_during_analysis_blocks_entry_and_invalidates_read(desk):
    packet=desk.prepare(Market(),clock=CLOCK)
    desk.lab.pause()
    with pytest.raises(ValueError):desk.read_packet(CLOCK)
    result=desk.submit(decision(packet),Market(),CLOCK)
    assert 'пауза' in result['result']
    assert not desk.lab.overview()['trades']


def test_pause_during_refresh_blocks_entry(desk):
    packet=desk.prepare(Market(),clock=CLOCK)
    class Pausing(Market):
        def snapshot(self,*a,**kw):
            desk.lab.pause()
            return super().snapshot(*a,**kw)
    assert 'пауза' in desk.submit(decision(packet),Pausing(),CLOCK)['result']
    assert not desk.lab.overview()['trades']


@pytest.mark.parametrize('change',[{'uid':'outside'},{'quantity':1000},{'mode':'live'},{'confidence':float('nan')},{'action':'order'}])
def test_unprepared_contract_and_extra_trade_controls_rejected(desk,change):
    packet=desk.prepare(Market(),clock=CLOCK)
    with pytest.raises(ValueError):desk.submit(decision(packet,**change),Market(),CLOCK)
    assert not desk.lab.overview()['trades']


@pytest.mark.parametrize('kind',['margin','closed','stale','confidence'])
def test_independent_risk_and_market_checks(desk,kind):
    market=Market();packet=desk.prepare(market,clock=CLOCK)
    if kind=='margin':market.margin='18000'
    if kind=='closed':market.tradeable=False
    if kind=='stale':market.clock=CLOCK-timedelta(hours=1)
    proposal=decision(packet,confidence=.1) if kind=='confidence' else decision(packet)
    result=desk.submit(proposal,market,CLOCK)
    assert result['result'] and not desk.lab.overview()['trades']


def test_hold_returns_to_hub_without_fetching_market_or_trading(desk):
    packet=desk.prepare(Market(),clock=CLOCK)
    class NoNetwork:
        def snapshot(self,*args):raise AssertionError('No need to fetch for hold with no positions')
    result=desk.submit(decision(packet,action='hold',uid=''),NoNetwork(),CLOCK)
    assert 'hold' in result['result'] and not desk.lab.overview()['trades']
    assert desk.latest()['status']=='complete'


def test_connector_worker_does_not_call_paid_model(desk):
    called=[]
    desk.lab.cycle(Market(),CLOCK,ai=lambda *a:called.append(a))
    assert not called and not desk.lab.overview()['trades']


def test_universe_is_bounded_and_diversifies_nearest_contracts():
    from invest_blog.trading_market import FuturesMarket
    class API:
        def call(self,service,method,payload):
            assert method=='Futures'
            rows=[]
            for i in range(12):
                for series in (10,30):
                    rows.append({'uid':f'{i}-{series}','ticker':f'F{i}{series}','basicAsset':f'asset{i}',
                        'apiTradeAvailableFlag':True,'expirationDate':(CLOCK+timedelta(days=series)).isoformat()})
            rows.append({'uid':'expired','apiTradeAvailableFlag':True,'expirationDate':CLOCK.isoformat()})
            return {'instruments':rows}
    result=FuturesMarket('',None,API()).universe(CLOCK)
    assert len(result['uids'])==8 and all(uid.endswith('-10') for uid in result['uids'])
    assert result['eligible_contracts']==24


def test_sample_uses_candidates_without_mutating_watchlist(desk):
    class Sample(Market):
        def universe(self,clock):return {'uids':['b'],'eligible_contracts':100,'selection':'Bounded test sample'}
    packet=desk.prepare(Sample(),'sample',CLOCK)
    assert packet['candidates'][0]['uid']=='b'
    assert list(desk.lab.overview()['state']['watchlist'])==['a']
    desk.submit(decision(packet,uid='b'),Sample(),CLOCK)
    assert 'b' in desk.lab.overview()['state']['watchlist']


def test_isolated_transport_auth_origin_schema_and_tools(desk,monkeypatch):
    token='S'*48
    with TestClient(create_connector(desk.lab.path,lambda:Market(),token)) as client:
        payload={'jsonrpc':'2.0','id':1,'method':'tools/list'}
        assert client.post('/mcp',json=payload).status_code==401
        assert client.post('/mcp?token='+token,json=payload).status_code==401
        headers={'Authorization':'Bearer '+token}
        assert client.post('/mcp',json=payload,headers={**headers,'Origin':'https://evil.test'}).status_code==403
        response=client.post('/mcp',json=payload,headers=headers)
        assert response.headers['cache-control']=='no-store'
        tools=response.json()['result']['tools']
        assert [t['name'] for t in tools]==['get_prepared_market_analysis','submit_paper_decision']
        assert client.get('/api/trading/overview',headers=headers).status_code==404
        assert client.get('/api/editor-token',headers=headers).status_code==404
        assert client.get('/docs',headers=headers).status_code==404
        assert client.get('/mcp',headers=headers).status_code==405
        init=client.post('/mcp',headers=headers,json={**payload,'method':'initialize','params':{'protocolVersion':'2025-06-18'}}).json()
        assert init['result']['protocolVersion']=='2025-06-18'
        assert client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','method':'notifications/initialized'}).status_code==202
        assert client.post('/mcp',headers=headers,json=[payload]).json()['error']['code']==-32600
        assert client.post('/mcp',headers=headers,content='x'*33000).status_code==415
        assert client.post('/mcp',headers={**headers,'content-type':'application/json'},content='x'*33000).status_code==413
        rpc={**payload,'method':'tools/call','params':{'name':'submit_paper_decision','arguments':{'mode':'live'}}}
        assert client.post('/mcp',headers=headers,json=rpc).json()['result']['isError']


def test_connector_needs_strong_independent_token(tmp_path):
    with pytest.raises(ValueError):create_connector(tmp_path/'lab.db',lambda:Market(),token='short')


def test_prepare_route_requires_csrf_and_valid_scope(tmp_path):
    from invest_blog.dashboard import create_app
    with TestClient(create_app(tmp_path/'app.db')) as client:
        assert client.post('/api/trading/analysis',json={'scope':'sample'}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        assert client.post('/api/trading/analysis',headers=headers,json={'scope':'all_secrets'}).status_code==422
        assert client.get('/api/trading/analysis').json()['latest'] is None
