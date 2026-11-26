"""Versioned demo entry guards. These functions never change protective exits."""
from decimal import Decimal

FUTURES_VERSION='t-futures-v3-20261126'
WALT_VERSION='walt-v3-20261126'
MIN_TREND_ATR=Decimal('.2')

def strong_trend(fast,slow,atr):
    try:
        fast,slow,atr=map(lambda x:Decimal(str(x)),(fast,slow,atr))
        return all(x.is_finite() and x>0 for x in (fast,slow,atr)) and abs(fast-slow)>=MIN_TREND_ATR*atr
    except (ValueError,ArithmeticError):return False

def record_close(state,key,at,is_stop):
    if not is_stop:
        state.setdefault('stop_history',{}).pop(key,None)
        state.setdefault('reentry_required',{}).pop(key,None)
        return
    recent=[t for t in state.setdefault('stop_history',{}).get(key,[]) if 0<=at-t<=86400]
    recent.append(at);state['stop_history'][key]=recent[-2:]
    state.setdefault('reentry_required',{})[key]=at
    state.setdefault('cooldowns',{})[key]=at+(14400 if len(recent)>=2 else 3600)

def walt_entry_block(state,row,now):
    if state.get('strategy_version')!=WALT_VERSION:return None
    key=str(row['market_id']);side=row['side'];m=row.get('metrics',{});scenario=row.get('scenario',{})
    if now<float(state.get('cooldowns',{}).get(key,0)):return 'Пауза после стопа: час, после двух стопов подряд — четыре часа.'
    if not strong_trend(m.get('ema20'),m.get('ema50'),m.get('atr14')):return 'Слабый тренд: расстояние EMA20/EMA50 меньше 0,2 ATR или нет данных.'
    try:
        close,fast,slow=(Decimal(str(m[k])) for k in ('close','ema20','ema50'))
        if not close.is_finite() or close<=0:return 'Цена закрытой свечи не подтверждена.'
        aligned=close>fast>slow if side=='long' else close<fast<slow
        if not aligned:return 'Числовые EMA и закрытие не подтверждают направление.'
    except (KeyError,ValueError,ArithmeticError):return 'Числовое направление не подтверждено.'
    expected='up' if side=='long' else 'down'
    if scenario.get('trend_1h')!=expected or scenario.get('trend_4h')!=expected:return 'Направление не подтверждено одновременно на 1ч и 4ч.'
    stopped=state.get('reentry_required',{}).get(key)
    if stopped is not None:
        try:
            if not float(stopped)<float(row['cutoff'])<=now:return 'После стопа ещё нет новой закрытой часовой свечи.'
            crossed=Decimal(str(m['close']))>Decimal(str(scenario['resistance'])) if side=='long' else Decimal(str(m['close']))<Decimal(str(scenario['support']))
            if not crossed or scenario.get('breakout_volume_confirmed') is not True:return 'Повторный вход ждёт нового пробоя с подтверждённым объёмом.'
        except (KeyError,ValueError,ArithmeticError):return 'Повторный вход: данные пробоя не подтверждены.'
    return None

def checked_explanation(proposal,features,trend):
    """Show comparisons calculated in code instead of unchecked model prose."""
    p=dict(proposal);f={k:Decimal(str(features[k])) for k in ('last_close','sma_fast','sma_slow','atr')}
    if any(not v.is_finite() or v<=0 for v in f.values()):raise ValueError('Invalid numeric evidence')
    action=p['action'];direction='long' if f['last_close']>f['sma_fast']>f['sma_slow'] else 'short' if f['last_close']<f['sma_fast']<f['sma_slow'] else 'wait'
    if action in ('long','short') and (direction!=action or trend.get('direction')!=action or not strong_trend(trend.get('sma10'),trend.get('sma30'),trend.get('atr14'))):
        p.update(action='hold',confidence=0)
    label={'long':'лонг','short':'шорт','hold':'ожидание','close':'закрытие'}[p['action']]
    p['reason']=(f'Решение ИИ: {label}. Проверено кодом: закрытие 15м {f["last_close"]}; '
                 f'SMA10 {f["sma_fast"]}, SMA30 {f["sma_slow"]}, ATR14 {f["atr"]}; '
                 f'направление 15м {direction}, 1ч {trend.get("direction","unknown")}. '
                 'Самооценка модели не является вероятностью прибыли.')[:1000]
    return p
