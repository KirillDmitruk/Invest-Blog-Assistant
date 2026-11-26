from copy import deepcopy
from invest_blog.demo_notifications import walt_summary, futures_summary, result
from test_paper_perps import opened, NOW

def test_walt_open_includes_equity_return_and_protection():
    e,_=opened();text=walt_summary(e.state,e.changes)
    assert '🧪 Walt · ДЕМО' in text and 'Открыта · BTC' in text
    assert 'От старта:' in text and 'Стоп ' in text and 'тейк ' in text
    assert 'Открытые позиции · 1' in text and 'маржа ' in text
    assert 'Рыночные сигналы' not in text and len(text)<1500

def test_walt_closed_uses_net_including_entry_exit_and_funding():
    e,_=opened();e.changes=[];p=e.state['positions']['1'];p['funding_paid']='3'
    e.close('1','110',NOW+60,'take');e.values()
    t=e.state['closed'][-1];text=walt_summary(e.state,e.changes)
    assert result(t['net_pnl'],t['margin'],'$') in text
    assert 'Закрыта · BTC' in text and 'после комиссий и фандинга' in text
    assert 'Открытые позиции · 0' in text

def future_data():
    p={'ticker':'BRZ6','side':'short','contracts':2,'entry_fee':'20','entry_margin':'10000','reserved_margin':'12000','accrued_pnl':'520','stop':'101','target':'90'}
    before={'state':{'positions':{'u':p}},'trades':[]}
    after={'state':{'positions':{'u':p},'budget':'1000000','last_cycle':'2026-11-26T13:00:00+00:00'},'account':{'equity':'1000500'},'trades':[]}
    return before,after

def test_futures_open_position_percentage_uses_current_go_and_entry_fee():
    b,a=future_data();text=futures_summary(b,a)
    assert 'Т‑Инвестиции · ДЕМО' in text and '16:00 МСК' in text
    assert '+500,00 ₽ (+4,17%)' in text and '+500,00 ₽ (+0,05%)' in text

def test_futures_closed_uses_original_go_and_both_fees():
    b,a=future_data();a['state']['positions']={}
    t={'id':1,'uid':'u','ticker':'BRZ6','action':'close','detail':{'side':'short','contracts':2,'pnl_before_exit_fee':'520','fee':'20','entry_fee':None,'entry_margin':None,'reason':'Тейк'}}
    text=futures_summary(b,a,[t])
    assert '+480,00 ₽ (+4,80%)' in text and 'после комиссий' in text
    assert 'Открытые позиции · 0' in text

def test_unknown_basis_is_never_invented():
    assert '% нет данных' in result('10',None,'₽')
    b,a=future_data();b['state']['positions']={};a['state']['positions']={}
    t={'id':1,'uid':'x','ticker':'OLD','action':'close','detail':{'side':'long','contracts':1,'pnl_before_exit_fee':'100','fee':'2','reason':'Стоп'}}
    assert 'результат не подтверждён' in futures_summary(b,a,[t])

def test_walt_stale_snapshot_is_explicit():
    e,_=opened();e.state['warnings']=['Не удалось проверить BTC']
    assert '⚠️ Не удалось проверить BTC' in walt_summary(e.state)
