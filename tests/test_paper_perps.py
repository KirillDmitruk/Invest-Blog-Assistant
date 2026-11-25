"""Deterministic PAPER accounting with fake public data; no brokerage access."""
import copy,json
from decimal import Decimal
import pytest
from invest_blog.paper_perps import PaperEngine,initial,num

NOW=1800000000

def report(at=NOW,side='long',accepted=True):
    return {'created_at':str(at),'created_epoch':at,'ai':{'status':'ok'},'rows':[
        {'market_id':1,'symbol':'BTC','side':side,'ai_review':{'accept':accepted},'entry_blockers':[]}]}

class Market:
    def __init__(self):self.price='100';self.bars=[];self.fundings=[];self.fail=False
    def entry(self,ident,now):
        return {'symbol':'BTC','price':self.price,'at':now,'active':True,'max_leverage':20,'step':'.001',
            'min_quantity':'.001','min_notional':'1','maintenance':'.03'}
    def observe(self,ident,since,now):
        if self.fail:raise ValueError('data unavailable')
        return {**self.entry(ident,now),'bars':self.bars,'fundings':self.fundings}

def opened(side='long'):
    m=Market();e=PaperEngine(initial());e.cycle(report(side=side),m,NOW)
    assert len(e.state['positions'])==1
    return e,m

def test_initial_10000_and_no_forced_entry_without_ai_approval():
    e=PaperEngine(initial());e.cycle(report(accepted=False),Market(),NOW)
    assert e.state['cash']=='10000' and not e.state['positions']
    e.cycle(None,Market(),NOW+900);assert not e.state['positions']

@pytest.mark.parametrize('side',['long','short'])
def test_entry_fee_risk_caps_and_signal_dedup(side):
    e,m=opened(side);p=e.state['positions']['1']
    assert num(p['risk'])<=100 and p['leverage']<=3
    assert num(p['margin'])<=2500 and num(e.state['cash'])<10000
    e.cycle(report(side=side),m,NOW);assert len(e.state['positions'])==1 and len(e.state['seen'])==1
    assert len([x for x in e.state['events'] if x['kind']=='open'])==1

def test_intraminute_stop_and_take_chooses_stop_and_never_reopens_same_report():
    e,m=opened();p=e.state['positions']['1'];t=p['checked_until']
    m.bars=[{'t':t,'o':'100','h':p['take'],'l':p['stop'],'c':'100'}]
    e.cycle(report(),m,t+60)
    assert not e.state['positions'] and e.state['closed'][-1]['reason']=='stop'
    assert num(e.state['closed'][-1]['net_pnl'])<0
    e.cycle(report(),m,t+60);assert not e.state['positions']

def test_gap_at_open_does_not_fill_at_unavailable_stop_price():
    e,m=opened();p=e.state['positions']['1'];t=p['checked_until']
    m.bars=[{'t':t,'o':'90','h':'92','l':'88','c':'91'}]
    e.cycle(report(),m,t+60)
    assert num(e.state['closed'][-1]['exit'])<90

def test_hourly_funding_payer_and_receiver_not_double_charged():
    for side,sign in [('long',-1),('short',1)]:
        e,m=opened(side);p=e.state['positions']['1'];before=num(e.state['cash']);t=p['checked_until']
        m.fundings=[{'t':NOW+30,'value':'.01','direction':'long'}]
        e.cycle(report(side=side),m,NOW+30)
        after=num(e.state['cash']);assert (after-before)*sign>0
        e.cycle(report(side=side),m,NOW+30);assert num(e.state['cash'])==after

def test_missing_bars_pauses_new_entries_without_fabricated_close():
    e,m=opened();e.cycle(report(NOW+900),m,NOW+900)
    assert 'data_error' in e.state['positions']['1'] and len(e.state['positions'])==1
    assert not e.state['closed'] and e.state['warnings']

def test_first_partially_elapsed_entry_bar_is_not_replayed():
    m=Market();e=PaperEngine(initial());start=NOW+17;e.cycle(report(start),m,start)
    # This low occurred before the hypothetical entry; never use it to close.
    m.bars=[{'t':NOW,'o':'100','h':'110','l':'1','c':'100'}]
    e.cycle(report(start),m,NOW+60);assert e.state['positions']

def test_stale_report_and_unknown_direction_do_not_open():
    for p in (report(NOW-10801),report(side='wait')):
        e=PaperEngine(initial());e.cycle(p,Market(),NOW);assert not e.state['positions']

def test_daily_loss_pause_does_not_disable_position_monitoring():
    e,m=opened();e.state['day_start']='11000';e.cycle(report(),m,NOW)
    assert e.state['paused'] and e.state['positions']

def test_stopped_account_balance_includes_all_entry_exit_costs_and_funding():
    e,m=opened();p=e.state['positions']['1'];t=p['checked_until']
    m.fundings=[{'t':NOW+30,'value':'.01','direction':'long'}]
    e.cycle(report(),m,NOW+30)
    m.bars=[{'t':t,'o':'100','h':'101','l':p['stop'],'c':p['stop']}]
    e.cycle(report(),m,t+60)
    trade=e.state['closed'][-1]
    assert num(e.state['cash'])==Decimal(10000)+num(trade['net_pnl'])

def test_new_entry_risk_uses_current_mark_to_stop_not_only_original_risk():
    e,m=opened();before=num(e.state['positions']['1']['risk']);m.price='105'
    e.cycle(report(),m,NOW)
    assert num(e.state['positions']['1']['risk'])>before

def test_entire_cycle_contains_no_secret_or_real_execution_dependencies():
    import inspect
    import invest_blog.paper_perps as module
    source=inspect.getsource(module)
    assert 'requests' not in source and 'perps_execute' not in source and 'WaltAgent' not in source

def test_equity_history_uses_saved_net_values_and_deduplicates_cycles():
    e,m=opened();p=e.state['equity_history'][-1]
    assert p['at']==NOW and p['equity']==e.state['equity'] and num(p['equity'])<10000
    e.cycle(None,m,NOW)
    assert len([r for r in e.state['equity_history'] if r['at']==NOW])==1

def test_history_is_bounded_and_preserves_partial_data_flag():
    from invest_blog.paper_perps import record_equity
    s=initial();s['warnings']=['Missing quote'];s['equity_history']=[{'at':i,'equity':'10000'} for i in range(1,2020)]
    record_equity(s,2020)
    assert len(s['equity_history'])==2016
    assert s['equity_history'][-1]['partial'] is True
    assert s['equity_history'][-1]['at']==2020

def test_saved_cycle_value_wins_over_earlier_strategy_baseline():
    from invest_blog.paper_perps import record_equity
    s=initial();s['strategy_baseline']={'at':100,'equity':'10000'};s['equity_history']=[{'at':100,'equity':'9990'}]
    record_equity(s,200)
    assert next(r for r in s['equity_history'] if r['at']==100)['equity']=='9990'
