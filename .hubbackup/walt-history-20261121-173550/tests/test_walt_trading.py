"""Deterministic money maths and manual one-shot execution. All writes are mocked."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json,time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from invest_blog.walt_agent import WaltAgent,TRADE_SCOPE
from invest_blog.walt_trading import WaltTrading,DEFAULT_LIMITS,calculate,number
from test_walt_agent import crypto


def contract(ident=0):
    return {'contract_id':ident,'base_asset':'BTC','contract_name':'BTC-USDC','quote_asset':'USDC',
        'is_active':True,'tick_size':'.1','step_size':'.001','size_decimals':3,'price_decimals':1,
        'min_size_quote':'1','max_size_quote':'25000','min_size_base':'.001','max_size_base':'100000',
        'max_leverage':20,'taker_fee_pct':'.0009','price_slippage':'.01','tpsl_buffer_pct':'.0002',
        'funding_rate_hourly':'.0001','volume_24h_usd':'1000000','open_interest_usd':'10000'}


def wrapped(value):return {'content':[{'type':'text','text':json.dumps(value)}]}


class FakeAgent(WaltAgent):
    def __init__(self,path):
        super().__init__(path,crypto=crypto)
        self.save_tokens({'access_token':'SECRET','scope':TRADE_SCOPE},expected_scope=TRADE_SCOPE)
        self.positions=[];self.executions=0;self.prepares=0;self.fail=False;self.price_value='100';self.balance='1000'
        self.preview_liq='80';self.echo=True;self.protection=True;self.currency='USDC'
    def connect(self):return 'session'
    def rpc(self,method,params=None,session_id=None,_manual=False):
        name=params['name'];args=params.get('arguments',{})
        if name=='perps_list_contracts':value={'contracts':[contract()]}
        elif name=='balance_get':value={'accounts':[{'currency':self.currency,'balance_available':self.balance}]}
        elif name=='perps_list_positions':value={'positions':self.positions}
        elif name=='perps_latest_rate':value={'rate':self.price_value,'market_id':'BTC-USD-PERP'}
        elif name=='perps_prepare_order_place':
            assert _manual;self.prepares+=1;self.last_args=args
            value={'ok':True,'action':{'kind':'order.place','params':args} if self.echo else {'kind':'unexpected'},
                'risk':{'liquidation_price':self.preview_liq},'warnings':[],
                'confirmationToken':'ONE_TIME_SECRET','expiresAt':(datetime.now(timezone.utc)+timedelta(seconds=90)).isoformat()}
        elif name=='perps_execute_operation':
            assert _manual and args=={'confirmationToken':'ONE_TIME_SECRET'};self.executions+=1
            if self.fail:raise TimeoutError('ambiguous')
            a=self.last_args
            self.positions=[{'contract_id':0,'side':a['side'],'avg_entry_price':'100','value_base':'0.4','margin_quote':a['amount'],
                'liquidation_price':self.preview_liq,'auto_close':{'stop_loss':{'trigger_price_quote':a['autoClose']['stopLoss']['priceQuote']},
                'take_profit':{'trigger_price_quote':a['autoClose']['takeProfit']['priceQuote']}} if self.protection else {}}]
            value={'order_id':'test-order'}
        else:raise AssertionError(name)
        return wrapped(value),'session'


@pytest.fixture
def service(tmp_path):
    agent=FakeAgent(tmp_path/'test.db');service=WaltTrading(agent)
    packet={'id':'signal','expires':time.time()+300,'candidates':[{'contract_id':0,'ai':{'side':'wait','reason':'Not confirmed'}}]}
    with agent.db() as db:db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',('signal',time.time(),json.dumps(packet)))
    return service


def quote(service):return service.quote('signal',0,'long')


@pytest.mark.parametrize('side',['long','short'])
def test_money_risk_fees_and_price_levels(side):
    p=calculate(contract(),'100','1000','1000',DEFAULT_LIMITS,side)
    assert p['leverage']==5 and Decimal(p['margin'])<=10
    assert Decimal(p['risk_estimate'])<=10 and Decimal(p['quantity_estimate'])>0
    if side=='long':assert Decimal(p['stop'])<100<Decimal(p['take']) and Decimal(p['funding_hour_estimate'])>0
    else:assert Decimal(p['take'])<100<Decimal(p['stop']) and Decimal(p['funding_hour_estimate'])<0


def test_negative_funding_direction_is_not_reversed():
    c=contract();c['funding_rate_hourly']='-.001'
    assert Decimal(calculate(c,100,1000,1000,DEFAULT_LIMITS,'long')['funding_hour_estimate'])<0
    assert Decimal(calculate(c,100,1000,1000,DEFAULT_LIMITS,'short')['funding_hour_estimate'])>0


def test_small_balance_is_not_forced_up_to_minimum():
    with pytest.raises(ValueError):calculate(contract(),100,5,5,DEFAULT_LIMITS,'long')


def test_contract_id_zero_is_preserved_and_calculation_has_no_write(service):
    result=quote(service)
    assert result['proposal']['contract_id']==0 and result['status']=='calculated'
    assert service.agent.prepares==0 and service.agent.executions==0


def test_usdt_account_is_supported_without_conversion_or_writes(service):
    service.agent.currency='USDT'
    p=quote(service)['proposal']
    assert p['currency']=='USDT' and p['quote_currency']=='USDC'
    assert p['free_balance']=='1000'
    assert service.agent.prepares==service.agent.executions==0


def test_unsupported_currency_is_not_silently_converted(service):
    service.agent.currency='BTC'
    with pytest.raises(ValueError,match='USDT/USDC'):quote(service)


def test_small_usdt_account_keeps_risk_limit_and_allows_lower_leverage(service):
    service.agent.currency='USDT';service.agent.balance='25.444395'
    with pytest.raises(ValueError,match='уменьшить плечо'):quote(service)
    limits=service.limits();service.save_limits({'leverage':2},limits['revision'])
    p=quote(service)['proposal']
    assert p['currency']=='USDT' and p['leverage']==2
    assert Decimal(p['risk_estimate'])<=Decimal('0.26')
    assert service.agent.prepares==service.agent.executions==0


def test_currency_change_invalidates_old_draft(service):
    draft=quote(service);service.agent.currency='USDT'
    with pytest.raises(ValueError,match='Валюта'):service.prepare(draft['id'])
    assert not service.agent.prepares


def test_changed_limits_invalidate_quote(service):
    draft=quote(service);l=service.limits();service.save_limits({'max_margin':'5'},l['revision'])
    with pytest.raises(ValueError,match='Лимиты'):service.prepare(draft['id'])
    assert not service.agent.prepares


def test_price_drift_requires_recalculation(service):
    draft=quote(service);service.agent.price_value='102'
    with pytest.raises(ValueError,match='Цена'):service.prepare(draft['id'])


def test_preview_has_no_execution_and_never_exposes_token(service):
    draft=quote(service);preview=service.prepare(draft['id'])
    assert preview['status']=='previewed' and service.agent.executions==0
    assert 'ONE_TIME_SECRET' not in json.dumps(preview)
    assert 'ONE_TIME_SECRET' not in json.dumps(service.overview())


@pytest.mark.parametrize('kind',['no_liq','liq_before_stop','wrong_action'])
def test_unverified_protection_or_action_is_blocked(service,kind):
    if kind=='no_liq':service.agent.preview_liq=None
    if kind=='liq_before_stop':service.agent.preview_liq='98'
    if kind=='wrong_action':service.agent.echo=False
    draft=quote(service);preview=service.prepare(draft['id'])
    assert preview['status']=='blocked'
    with pytest.raises(ValueError):service.execute(draft['id'],preview['preview_hash'],'OPEN_REAL_POSITION')
    assert service.agent.executions==0


def test_execute_requires_matching_confirmation_and_fresh_hash(service):
    draft=quote(service);p=service.prepare(draft['id'])
    with pytest.raises(ValueError):service.execute(draft['id'],p['preview_hash'],'yes')
    with pytest.raises(ValueError):service.execute(draft['id'],'wrong','OPEN_REAL_POSITION')
    assert service.agent.executions==0


def test_success_verifies_stops_and_repeat_does_not_execute_twice(service):
    draft=quote(service);p=service.prepare(draft['id'])
    first=service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    second=service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert first['status']==second['status']=='protected' and service.agent.executions==1


def test_ambiguous_send_never_retries(service):
    draft=quote(service);p=service.prepare(draft['id']);service.agent.fail=True
    first=service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert first['status']=='uncertain'
    service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert service.agent.executions==1


def test_concurrent_confirmation_sends_once(service):
    draft=quote(service);p=service.prepare(draft['id'])
    def send():
        try:return service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:send(),range(2)))
    assert service.agent.executions==1


def test_expired_provider_preview_cannot_execute(service):
    draft=quote(service);p=service.prepare(draft['id'])
    row,payload=service.row(draft['id']);payload['preview_expires']=0
    with service.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(payload),draft['id']))
    with pytest.raises(ValueError):service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert service.agent.executions==0


def test_prepare_cannot_replace_token_during_execution(service):
    draft=quote(service)
    with service.agent.db() as db:db.execute('INSERT OR REPLACE INTO walt_trade_lock VALUES(1,?,?)',('other',time.time()+30))
    with pytest.raises(ValueError):service.prepare(draft['id'])
    assert service.agent.prepares==0


def test_missing_stop_after_execution_alerts_and_blocks_new_entries(service):
    draft=quote(service);p=service.prepare(draft['id']);service.agent.protection=False
    result=service.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert result['status']=='uncertain' and 'СРОЧНО' in result['alert']


def test_read_only_grant_cannot_reach_manual_write(tmp_path):
    agent=WaltAgent(tmp_path/'test.db',crypto=crypto)
    agent.save_tokens({'access_token':'SECRET','scope':'perps.readonly'})
    with pytest.raises(ValueError):agent.rpc('tools/call',{'name':'perps_prepare_order_place','arguments':{}},_manual=True)
    with pytest.raises(ValueError):agent.rpc('tools/call',{'name':'perps_execute_operation','arguments':{}},_manual=True)


def test_signal_ids_and_expiry_are_enforced(service):
    with pytest.raises(ValueError):service.quote('unknown',0,'long')
    with pytest.raises(ValueError):service.quote('signal',999,'long')
    with service.agent.db() as db:db.execute('UPDATE walt_trade_signals SET payload=?',(json.dumps({'id':'signal','expires':0,'candidates':[]}),))
    with pytest.raises(ValueError):quote(service)


def test_model_cannot_turn_single_snapshot_into_a_trade_signal(service):
    service.agent.local_request=lambda *a,**kw:{'message':{'content':json.dumps({'signals':[
        {'contract_id':0,'side':'long','reason':'Цена растёт, OI растёт, сильный недельный тренд'}]})}}
    packet=service.signal()
    assert packet['candidates'][0]['ai']['side']=='wait'
    assert 'сильный недельный тренд' not in packet['candidates'][0]['ai']['reason']
    assert service.agent.executions==service.agent.prepares==0


def test_old_cached_recommendations_are_suppressed(service):
    packet={'id':'signal','expires':time.time()+300,'candidates':[{'contract_id':0,
        'ai':{'side':'long','reason':'OI растёт'}}]}
    with service.agent.db() as db:db.execute('UPDATE walt_trade_signals SET payload=?',(json.dumps(packet),))
    assert service.overview()['latest_signal']['candidates'][0]['ai']['side']=='wait'
    assert quote(service)['ai']['side']=='wait'


def test_full_catalogue_scan_includes_every_contract_and_keeps_failed_quote(service):
    original=service.agent.rpc;requested=[]
    def rpc(method,params=None,*args,**kwargs):
        if params['name']=='perps_list_contracts':
            contracts=[]
            for ident in range(138):
                c=contract(ident);c['base_asset']='ASSET'+str(ident);contracts.append(c)
            return wrapped({'contracts':contracts}),'session'
        if params['name']=='perps_latest_rate':
            symbol=params['arguments']['marketId'];requested.append(symbol)
            if symbol=='ASSET13-USD-PERP':raise ValueError('unavailable')
        return original(method,params,*args,**kwargs)
    service.agent.rpc=rpc
    service.agent.local_request=lambda *a,**kw:pytest.fail('No invented directional analysis without history')
    packet=service.signal()
    assert len(requested)==len(set(requested))==138
    assert len(packet['candidates'])==138 and packet['total']==138
    assert packet['quotes_available']==137 and packet['errors']==['ASSET13']
    assert packet['candidates'][13]['price_available'] is False
    assert all(r['ai']['side']=='wait' for r in packet['candidates'])
    assert service.scan_progress()['done']==138
    assert service.scan_progress()['status']=='complete'
    with pytest.raises(ValueError,match='котировку'):service.quote(packet['id'],13,'long')
    assert service.agent.executions==service.agent.prepares==0


def test_api_csrf_and_limits(tmp_path):
    from invest_blog.dashboard import create_app
    with TestClient(create_app(tmp_path/'app.db')) as client:
        assert client.post('/api/walt-trading/signal').status_code==403
        assert client.post('/api/walt-trading/auth').status_code==403
        assert client.post('/api/walt-trading/quote',json={'signal_id':'x','contract_id':0,'side':'long'}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/editor-token').json()['token']}
        limits=client.get('/api/walt-trading/overview').json()['limits']
        assert client.put('/api/walt-trading/limits',headers=headers,json={**limits,'leverage':10}).status_code==422
        assert client.put('/api/walt-trading/limits',headers=headers,json={**limits,'stop_pct':'NaN'}).status_code==422
        assert client.put('/api/walt-trading/limits',headers=headers,json={**limits,'stop_pct':'8','take_pct':'2'}).status_code==422
        assert client.post('/api/walt-trading/drafts/x/execute',headers=headers,json={'preview_hash':'x'*64,'confirm':'yes'}).status_code==422
