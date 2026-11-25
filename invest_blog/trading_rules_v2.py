"""Deterministic entry safeguards. No broker writes and no budget resets."""
from datetime import datetime,timedelta
from decimal import Decimal
from .model import iso_dt,quotation

VERSION='t-futures-v2-20261125'

def hour_trend(rows,clock):
    bars={}
    for row in rows:
        if row.get('isComplete') is not True:continue
        stamp=iso_dt(row['time'])
        if stamp.tzinfo is None or stamp+timedelta(hours=1)>clock:continue
        values={k:quotation(row.get(k)) for k in ('open','high','low','close')}
        if any(not v.is_finite() or v<=0 for v in values.values()) or not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:
            raise ValueError('Invalid hourly OHLC')
        if stamp in bars and bars[stamp]!=values:raise ValueError('Conflicting hourly candles')
        bars[stamp]=values
    stamps=sorted(bars)
    if len(stamps)<35 or clock-stamps[-1]>timedelta(hours=2):raise ValueError('Insufficient fresh hourly history')
    closes=[bars[t]['close'] for t in stamps];fast=sum(closes[-10:])/10;slow=sum(closes[-30:])/30;last=closes[-1]
    direction='long' if last>fast>slow else 'short' if last<fast<slow else 'wait'
    return {'direction':direction,'last_close':str(last),'sma10':str(fast),'sma30':str(slow),'candle_at':stamps[-1].isoformat()}

def activate(lab,clock):
    from .trading_lab import account_values
    with lab.db() as db:
        state=lab._load(db)
        if state.get('strategy_version')==VERSION:return False
        baseline={'at':clock.isoformat(),'equity':account_values(state)['equity'],'cash':state['cash'],
                  'trade_count':db.execute('SELECT COUNT(*) FROM lab_trades').fetchone()[0]}
        state['strategy_version']=VERSION;state['strategy_baseline']=baseline
        state.setdefault('cloud',{})['next_analysis']=None
        state.setdefault('strategy_changes',[]).append({'version':VERSION,**baseline})
        state['cooldowns']={}
        for uid,at,payload in db.execute("SELECT uid,at,payload FROM lab_trades WHERE action='close' ORDER BY id"):
            import json
            if json.loads(payload).get('reason','').startswith('Стоп'):
                until=iso_dt(at)+timedelta(hours=1)
                if until>clock:state['cooldowns'][uid]=until.isoformat()
        state['config_revision']+=1;lab._save(db,state)
    return True

def entry_block(state,uid,snapshot,clock):
    if state.get('strategy_version')!=VERSION:return None
    until=state.get('cooldowns',{}).get(uid)
    if until and iso_dt(until)>clock:return 'После стопа действует часовая пауза по контракту до '+until+'.'
    trend=snapshot.get('trend_1h',{})
    stamp=trend.get('candle_at')
    if not stamp or not timedelta(hours=1)<=clock-iso_dt(stamp)<=timedelta(hours=2):return 'Нет свежего подтверждения по закрытым часовым свечам.'
    return None
