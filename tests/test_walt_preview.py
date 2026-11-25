"""Regression from Walt's actual snake_case preview, without broker execution."""
from copy import deepcopy
from decimal import Decimal
import json
import pytest
from invest_blog.walt_preview import verify_action,verify_risk,conservative_boundary,protection_evidence
from invest_blog.walt_trading import WaltTrading
from invest_blog.walt_news import NoTradingNews
from test_walt_trading import contract,FakeAgent,NoHistory,wrapped


EXPECTED={'contractId':110,'side':'short','type':'market','leverage':4,'amount':'2.24','price':'130.182',
          'autoClose':{'takeProfit':{'priceQuote':'117.164'},'stopLoss':{'priceQuote':'134.739'}}}
ACTION={'kind':'order.place','request':{'contract_id':110,'side':'short','type':'market','leverage':4,
        'amount':'2.24','price':'130.182','auto_close':{'take_profit':{'price_quote':'117.164'},
        'stop_loss':{'price_quote':'134.739'}},'account_type':'bot_regular'}}
RISK={'ok':True,'checks':[{'name':key,'ok':True} for key in ('contract_active','leverage','amount','price')]}


def params(side='short'):
    return {'contract_id':110,'side':side,'leverage':4,'margin':'2.24','reference_price':'130.182',
            'stop':'134.739' if side=='short' else '125.625','take':'117.164' if side=='short' else '143.201'}


def metadata():
    c=contract(110);c.update(base_asset='NVDA',tick_size='.001',step_size='.001',funding_rate_hourly='.000032',
        margin={'min_initial_margin_fraction':500,'maintenance_margin_fraction':300,'closeout_margin_fraction':200})
    return c


def test_recorded_native_action_and_risk_are_recognized():
    verify_action(ACTION,EXPECTED);verify_risk(RISK)
    evidence=protection_evidence(params(),metadata(),RISK)
    assert evidence['source']=='local_conservative_estimate' and evidence['safe']
    assert Decimal(evidence['price'])>Decimal('134.739')


def test_decimal_formatting_is_equivalent_but_values_cannot_change():
    action=deepcopy(ACTION);action['request']['amount']='2.2400';verify_action(action,EXPECTED)
    action['request']['amount']='2.2401'
    with pytest.raises(ValueError):verify_action(action,EXPECTED)


@pytest.mark.parametrize('mutation',['account','kind','stop','missing_stop','extra','ambiguous','boolean'])
def test_wrong_or_ambiguous_action_stays_blocked(mutation):
    action=deepcopy(ACTION);r=action['request']
    if mutation=='account':r['account_type']='main'
    if mutation=='kind':action['kind']='position.close'
    if mutation=='stop':r['auto_close']['stop_loss']['price_quote']='134.740'
    if mutation=='missing_stop':del r['auto_close']['stop_loss']
    if mutation=='extra':r['reduce_only']=True
    if mutation=='ambiguous':r['contractId']=r['contract_id']
    if mutation=='boolean':r['leverage']=True
    with pytest.raises(ValueError):verify_action(action,EXPECTED)


@pytest.mark.parametrize('mutation',['failed','missing','false_ok'])
def test_provider_risk_failure_cannot_be_overridden(mutation):
    risk=deepcopy(RISK)
    if mutation=='failed':risk['checks'][0]['ok']=False
    if mutation=='missing':risk['checks'].pop()
    if mutation=='false_ok':risk['ok']=False
    with pytest.raises(ValueError):verify_risk(risk)


@pytest.mark.parametrize('side',['long','short'])
def test_boundary_keeps_positive_margin_reserves_at_estimated_stop(side):
    p=params(side);c=metadata();e=protection_evidence(p,c,RISK)
    assert e['safe']
    direction=1 if side=='long' else -1
    q=Decimal(e['quantity']);entry=Decimal(e['entry_bound']);fill=Decimal(e['stop_fill_estimate'])
    margin=min(Decimal(p['margin'])-q*entry*Decimal(c['taker_fee_pct']),q*entry/4)-Decimal(e['funding_24h_reserve'])
    balance_at_stop=margin+direction*q*(fill-entry)
    reserve_at_stop=q*fill*Decimal(e['reserved_margin_fraction'])
    assert balance_at_stop>reserve_at_stop
    assert direction*(fill-Decimal(e['price']))>Decimal(e['buffer'])


def test_stop_beyond_margin_boundary_is_blocked():
    p=params();p['stop']='180'
    assert protection_evidence(p,metadata(),RISK)['safe'] is False


def test_missing_or_wrong_fraction_units_do_not_get_a_guessed_estimate():
    c=metadata();del c['margin']
    with pytest.raises(ValueError):conservative_boundary(params(),c)
    c=metadata();c['margin']['min_initial_margin_fraction']='.05'
    with pytest.raises(ValueError):conservative_boundary(params(),c)


def test_receivable_funding_never_increases_margin_headroom():
    c=metadata();a=conservative_boundary(params(),c)
    c['funding_rate_hourly']='0';b=conservative_boundary(params(),c)
    assert a['price']==b['price'] and Decimal(a['funding_24h_reserve'])==0


@pytest.mark.parametrize('value',['NaN','-1','0',True])
def test_invalid_provider_liquidation_does_not_silently_fall_back(value):
    with pytest.raises(ValueError):protection_evidence(params(),metadata(),{**RISK,'liquidation_price':value})


def native_service(tmp_path):
    agent=FakeAgent(tmp_path/'native.db');original=agent.rpc;agent.fee='.0009'
    def rpc(method,params=None,*args,**kw):
        value,sid=original(method,params,*args,**kw)
        data=json.loads(value['content'][0]['text'])
        if params['name']=='perps_list_contracts':
            data['contracts'][0]['margin']=metadata()['margin'];data['contracts'][0]['taker_fee_pct']=agent.fee
        if params['name']=='perps_prepare_order_place':
            a=params['arguments'];data['action']={'kind':'order.place','request':{
                'contract_id':a['contractId'],'side':a['side'],'type':a['type'],'leverage':a['leverage'],'amount':a['amount'],'price':a['price'],
                'auto_close':{'take_profit':{'price_quote':a['autoClose']['takeProfit']['priceQuote']},'stop_loss':{'price_quote':a['autoClose']['stopLoss']['priceQuote']}},'account_type':'bot_regular'}}
            data['risk']=deepcopy(RISK)
        return wrapped(data),sid
    agent.rpc=rpc;service=WaltTrading(agent,history_factory=NoHistory,news_factory=NoTradingNews)
    with agent.db() as db:db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',('signal',0,json.dumps({'expires':4_000_000_000,'candidates':[{'contract_id':0,'ai':{'side':'wait','reason':'manual'}}]})))
    return service


def test_native_preview_is_reviewable_and_never_executes_automatically(tmp_path):
    s=native_service(tmp_path);draft=s.quote('signal',0,'long');p=s.prepare(draft['id'])
    assert p['status']=='previewed' and p['proposal']['liquidation_source']=='local_conservative_estimate'
    assert s.agent.executions==0
    assert 'ONE_TIME_SECRET' not in json.dumps(p)


def test_native_preview_executes_once_only_after_manual_confirmation_in_mock(tmp_path):
    s=native_service(tmp_path);draft=s.quote('signal',0,'long');p=s.prepare(draft['id'])
    with pytest.raises(ValueError):s.execute(draft['id'],p['preview_hash'],'')
    assert s.agent.executions==0
    result=s.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert result['status']=='protected' and s.agent.executions==1
    s.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert s.agent.executions==1


def test_changed_contract_after_preview_requires_new_manual_preview(tmp_path):
    s=native_service(tmp_path);draft=s.quote('signal',0,'long');p=s.prepare(draft['id']);s.agent.fee='.0010'
    with pytest.raises(ValueError,match='Параметры защиты'):s.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert s.agent.executions==0


def test_local_estimate_never_overrides_failed_provider_risk_checks(tmp_path):
    s=native_service(tmp_path);original=s.agent.rpc
    def rpc(method,params=None,*args,**kw):
        value,sid=original(method,params,*args,**kw)
        if params['name']=='perps_prepare_order_place':
            data=json.loads(value['content'][0]['text']);data['risk']['checks'][0]['ok']=False
            return wrapped(data),sid
        return value,sid
    s.agent.rpc=rpc;draft=s.quote('signal',0,'long');p=s.prepare(draft['id'])
    assert p['status']=='blocked' and s.agent.secret('trade:'+draft['id']) is None
    with pytest.raises(ValueError):s.execute(draft['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert s.agent.executions==0
