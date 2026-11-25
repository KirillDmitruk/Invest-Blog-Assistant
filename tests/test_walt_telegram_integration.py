"""Existing native Walt preview/limits reused; transport is a fake."""
import time
import json
import pytest
from datetime import datetime,timezone
from test_walt_preview import native_service
from invest_blog.telegram_walt import TradeBot
from invest_blog.walt_telegram_protocol import button

def test_public_market_button_uses_existing_fresh_preview_without_execution(tmp_path):
    service=native_service(tmp_path);sent=[]
    controller=TradeBot(service,'123456789','test',lambda text,markup:sent.append((text,markup)) or 42,lambda:None)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'rows':[{'market_id':0,'symbol':'BTC'}]}
    data=button(f'q:123:0:l:{int(time.time()+60)}','123456789','test')
    controller.handle(dict(data=data,owner='123456789',chat='123456789',message='1',query='first'),lambda run:report)
    assert 'Проверь сделку' in sent[0][0]
    assert 'Подтвердить открытие' in sent[0][1]['inline_keyboard'][0][0]['text']
    assert service.agent.executions==0

def test_old_group_classification_requires_new_preview(tmp_path):
    service=native_service(tmp_path);draft=service.quote('signal',0,'long')
    row,private=service.row(draft['id']);private['proposal']['group']='other'
    with service.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(private),draft['id']))
    with pytest.raises(ValueError,match='Классификация инструмента обновилась'):service.prepare(draft['id'])
    assert service.agent.executions==0

def test_tight_initial_margin_tries_lower_leverage_without_raising_limits(tmp_path):
    service=native_service(tmp_path);original=service.catalog;sent=[];before=service.limits()
    def catalog(sid):
        values=original(sid)
        values[0].update(max_leverage=3,margin={'min_initial_margin_fraction':3333,'maintenance_margin_fraction':2000,'closeout_margin_fraction':1333})
        return values
    service.catalog=catalog
    controller=TradeBot(service,'123456789','test',lambda text,markup:sent.append((text,markup)) or 42,lambda:None)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'rows':[{'market_id':0,'symbol':'BTC'}]}
    data=button(f'q:123:0:l:{int(time.time()+60)}','123456789','test')
    controller.handle(dict(data=data,owner='123456789',chat='123456789',message='1',query='lower'),lambda run:report)
    assert 'Плечо снижено с 3× до 2×' in sent[0][0]
    assert 'Подтвердить открытие' in sent[0][1]['inline_keyboard'][0][0]['text']
    assert service.limits()==before and service.agent.executions==0
    with service.agent.db() as db:rows=db.execute('SELECT status,payload FROM walt_trade_drafts ORDER BY at').fetchall()
    assert rows[-2]['status']=='blocked' and rows[-1]['status']=='previewed'
    assert json.loads(rows[-1]['payload'])['proposal']['leverage']==2

@pytest.mark.parametrize('leverage',[0,6,True])
def test_lower_leverage_api_rejects_invalid_or_increased_cap(tmp_path,leverage):
    service=native_service(tmp_path)
    with pytest.raises(ValueError,match='плечо превышает'):service.quote('signal',0,'long',leverage=leverage)

def test_confirm_keeps_buffered_amount_after_small_adverse_price_move(tmp_path):
    service=native_service(tmp_path);service.agent.balance='50'
    limits=service.limits();service.save_limits({'leverage':2},limits['revision'])
    draft=service.quote('signal',0,'long',reserve_price_drift=True);preview=service.prepare(draft['id'])
    amount=preview['proposal']['margin'];service.agent.price_value='100.2'
    service.execute(preview['id'],preview['preview_hash'],'OPEN_REAL_POSITION')
    assert service.agent.executions==1 and service.agent.last_args['amount']==amount
