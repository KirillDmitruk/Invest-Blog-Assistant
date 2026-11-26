"""Deterministic entry safeguards. No broker writes and no budget resets."""
from datetime import datetime,timedelta
from decimal import Decimal
from .model import iso_dt,quotation

from .trading_quality import FUTURES_VERSION as VERSION, strong_trend, record_close

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
    ranges=[max(bars[t]['high']-bars[t]['low'],abs(bars[t]['high']-closes[i-1]),abs(bars[t]['low']-closes[i-1])) for i,t in enumerate(stamps) if i]
    atr=sum(ranges[-14:])/14
    return {'atr14':str(atr),'direction':direction,'last_close':str(last),'sma10':str(fast),'sma30':str(slow),'candle_at':stamps[-1].isoformat()}

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
        state['cooldowns']={};state['stop_history']={};state['reentry_required']={}
        for uid,action,at,payload in db.execute('SELECT uid,action,at,payload FROM lab_trades ORDER BY id'):
            import json
            stamp=iso_dt(at).timestamp()
            if action.startswith('open_'):state['reentry_required'].pop(uid,None)
            elif action=='close' and 0<=clock.timestamp()-stamp<=86400:
                record_close(state,uid,stamp,json.loads(payload).get('reason','').startswith('Стоп'))
        state['config_revision']+=1;lab._save(db,state)
    return True

def entry_block(state,uid,snapshot,clock,features=None,side=None):
    if state.get('strategy_version')!=VERSION:return None
    until=state.get('cooldowns',{}).get(uid)
    if until and float(until)>clock.timestamp():return 'После стопа действует часовая пауза; после двух стопов подряд за сутки — четыре часа.'
    trend=snapshot.get('trend_1h',{})
    stamp=trend.get('candle_at')
    if not stamp or not timedelta(hours=1)<=clock-iso_dt(stamp)<=timedelta(hours=2):return 'Нет свежего подтверждения по закрытым часовым свечам.'
    if not strong_trend(trend.get('sma10'),trend.get('sma30'),trend.get('atr14')):return 'Слабый часовой тренд: расстояние SMA10/SMA30 меньше 0,2 ATR или нет данных.'
    stopped=state.get('reentry_required',{}).get(uid)
    if stopped is not None:
        if (iso_dt(stamp)+timedelta(hours=1)).timestamp()<=float(stopped):return 'После стопа ещё нет новой закрытой часовой свечи.'
        try:
            f=features or {};price=Decimal(f['last_close'])
            crossed=price>Decimal(f['breakout_high']) if side=='long' else price<Decimal(f['breakout_low'])
            if not crossed:return 'Повторный вход ждёт нового пробоя диапазона 20 закрытых свечей.'
            if (iso_dt(f['candle_at'])+timedelta(minutes=15)).timestamp()<=float(stopped):return 'Свеча пробоя должна закрыться после стопа.'
        except (KeyError,ValueError,ArithmeticError):return 'Повторный вход: данные пробоя не подтверждены.'
    return None
