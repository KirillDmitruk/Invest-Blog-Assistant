"""Actual fills replace preview estimates without changing the proposal."""
import json
from test_walt_trading import service,quote,wrapped


def test_nested_order_id_is_read_exactly_and_actual_position_preserves_proposal(service):
    original=service.agent.rpc;calls=[]
    def rpc(method,params=None,*args,**kw):
        calls.append(params)
        value,sid=original(method,params,*args,**kw)
        if params['name']=='perps_execute_operation':return wrapped({'order':{'order_id':'nested-order','status':'processing'}}),sid
        return value,sid
    service.agent.rpc=rpc
    d=quote(service);p=service.prepare(d['id']);proposal=p['proposal'].copy()
    result=service.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    assert result['status']=='protected'
    assert any(c['name']=='perps_get_order' and c['arguments']=={'orderId':'nested-order'} for c in calls)
    assert result['orders_checked']['orders'][0]['status']=='filled'
    assert result['actual_position']['liquidation_price']=='80'
    assert result['actual_position']['stop']==proposal['stop']
    assert result['proposal']==proposal
    service.agent.positions=[];service.sync_wallet()
    assert 'actual_position' not in service.public(d['id'])
    assert service.agent.executions==1


def test_processing_then_filled_is_resolved_by_reads_only(service):
    d=quote(service);p=service.prepare(d['id']);service.execute(d['id'],p['preview_hash'],'OPEN_REAL_POSITION')
    position=service.agent.positions[0];service.agent.positions=[]
    _,payload=service.row(d['id'])
    with service.agent.db() as db:db.execute("UPDATE walt_trade_drafts SET status='submitted' WHERE id=?",(d['id'],))
    first=service.reconcile(d['id']);assert first['status']=='submitted'
    service.agent.positions=[position]
    service.sync_wallet();result=service.public(d['id'])
    assert result['status']=='protected' and result['actual_position']['avg_entry_price']=='100'
    assert service.agent.executions==1
