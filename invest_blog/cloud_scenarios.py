"""Deterministic market scenarios from closed bars, not reversal probabilities."""
from .walt_history import indicator_series,technical_side,HOUR

ANCHORS=('BTC','ETH','SOL','NVDA')
TREND={'up':'рост','down':'снижение','range':'боковик'}
SCENARIOS={'up_continuation':'рост сохраняется','down_continuation':'снижение сохраняется',
    'rebound':'отскок против снижения','pullback':'откат внутри роста',
    'reversal_up_watch':'признаки разворота вверх','reversal_down_watch':'признаки разворота вниз',
    'range':'боковик / противоречивые сигналы'}


def closed_four_hour(bars):
    buckets={}
    for b in bars:buckets.setdefault(b['t']//(4*HOUR)*4*HOUR,[]).append(b)
    result=[]
    for start,group in sorted(buckets.items()):
        group=sorted(group,key=lambda x:x['t'])
        if [b['t'] for b in group]!=[start+i*HOUR for i in range(4)]:continue
        result.append({'t':start,'o':group[0]['o'],'c':group[-1]['c'],'h':max(b['h'] for b in group),
            'l':min(b['l'] for b in group),'v':sum(b.get('v',0) for b in group),'V':sum(b.get('V',0) for b in group)})
    return result


def trend(series):
    if len(series)<50:return 'range'
    m=series[-1];before=series[-4]
    if m['close']>m['ema20']>m['ema50'] and m['ema20']>before['ema20']:return 'up'
    if m['close']<m['ema20']<m['ema50'] and m['ema20']<before['ema20']:return 'down'
    return 'range'


def divergence(bars,series):
    """Pivots need two already closed bars to their right. No unconfirmed swing."""
    lows=[];highs=[]
    for i in range(max(2,len(bars)-72),len(bars)-2):
        neighbours=[bars[j] for j in range(i-2,i+3) if j!=i]
        if all(bars[i]['l']<b['l'] for b in neighbours):lows.append(i)
        if all(bars[i]['h']>b['h'] for b in neighbours):highs.append(i)
    bullish=len(lows)>=2 and len(bars)-1-lows[-1]<=12 and bars[lows[-1]]['l']<bars[lows[-2]]['l'] and series[lows[-1]]['rsi14']>series[lows[-2]]['rsi14']+5
    bearish=len(highs)>=2 and len(bars)-1-highs[-1]<=12 and bars[highs[-1]]['h']>bars[highs[-2]]['h'] and series[highs[-1]]['rsi14']<series[highs[-2]]['rsi14']-5
    return {'bullish':bool(bullish),'bearish':bool(bearish)}


def describe(bars):
    if len(bars)<240:raise ValueError('Для сценария нужно минимум 240 закрытых часов.')
    if any(b['t']-a['t']!=HOUR for a,b in zip(bars,bars[1:])):raise ValueError('Пропуски часовых свечей.')
    four=closed_four_hour(bars)
    if len(four)<50:raise ValueError('Недостаточно закрытых 4ч свечей.')
    series=indicator_series(bars);higher=indicator_series(four);m=series[-1];h=higher[-1]
    hourly=trend(series);macro=trend(higher);price=m['close'];atr=max(m['atr14'],price*1e-6)
    previous=bars[-25:-1];support=min(b['l'] for b in previous);resistance=max(b['h'] for b in previous)
    average=sum(b['V'] for b in previous)/24;relative=bars[-1]['V']/average if average>0 else None
    volume_ok=relative is not None and relative>=1.2
    breakout_up=price>resistance+.1*atr;breakout_down=price<support-.1*atr
    div=divergence(bars,series);flags=[]
    if m['rsi14']<30:flags.append('перепроданность: отскок возможен, разворот не доказан')
    if m['rsi14']>70:flags.append('перекупленность: откат возможен, разворот не доказан')
    if div['bullish']:flags.append('бычья RSI-дивергенция на подтверждённых впадинах')
    if div['bearish']:flags.append('медвежья RSI-дивергенция на подтверждённых вершинах')
    if (breakout_up or breakout_down) and not volume_ok:flags.append('выход за диапазон без подтверждения объёмом')
    if macro=='down':
        code='reversal_up_watch' if (breakout_up and volume_ok) or div['bullish'] else 'rebound' if hourly=='up' or price>m['ema20'] else 'down_continuation'
        direction='up' if code in ('reversal_up_watch','rebound') else 'down'
    elif macro=='up':
        code='reversal_down_watch' if (breakout_down and volume_ok) or div['bearish'] else 'pullback' if hourly=='down' or price<m['ema20'] else 'up_continuation'
        direction='down' if code in ('reversal_down_watch','pullback') else 'up'
    else:
        code='reversal_up_watch' if breakout_up and volume_ok else 'reversal_down_watch' if breakout_down and volume_ok else 'range'
        direction='up' if code=='reversal_up_watch' else 'down' if code=='reversal_down_watch' else 'range'
    if direction=='up':
        confirmation=max(resistance,h['ema20'])+.1*atr
        invalidation=max(price*1e-6,support-.1*atr)
        trigger='Ждать закрытия 4ч выше';cancel='Сценарий ослабнет при закрытии 1ч ниже'
    elif direction=='down':
        confirmation=max(price*1e-6,min(support,h['ema20'])-.1*atr)
        invalidation=resistance+.1*atr
        trigger='Ждать закрытия 4ч ниже';cancel='Сценарий ослабнет при закрытии 1ч выше'
    else:
        confirmation=resistance+.1*atr;invalidation=max(price*1e-6,support-.1*atr)
        trigger='Верхняя граница для наблюдения';cancel='Нижняя граница для наблюдения'
    # A level already crossed is still not a fresh trade instruction. A closed
    # 4h confirmation plus a retest is a scenario condition, not a fill guarantee.
    conditions={'confirmation':confirmation,'invalidation':invalidation,'confirmation_text':trigger,'invalidation_text':cancel,
        'requires':'Объём не ниже 1,2 среднего часа за сутки; затем удержание уровня/ретест. Это условие сценария, не приказ войти.'}
    aligned=hourly==macro and macro in ('up','down')
    advice={'rebound':'Не путать отскок с разворотом: старший тренд пока вниз.',
        'pullback':'Не путать коррекцию с разворотом: старший тренд пока вверх.',
        'reversal_up_watch':'Следить за подтверждением вверх; признаки разворота ещё не доказывают смену тренда.',
        'reversal_down_watch':'Следить за подтверждением вниз; признаки разворота ещё не доказывают смену тренда.',
        'up_continuation':'Приоритет наблюдения — продолжение роста; не догонять цену у сопротивления.',
        'down_continuation':'Приоритет наблюдения — продолжение снижения; не догонять падение у поддержки.',
        'range':'Направления нет: ждать выхода из диапазона с подтверждением.'}[code]
    return {'code':code,'label':SCENARIOS[code],'trend_1h':hourly,'trend_4h':macro,'aligned':aligned,
        'support':support,'resistance':resistance,'relative_volume':relative,'breakout_volume_confirmed':volume_ok,
        'divergence':div,'flags':flags,'conditions':conditions,'advice':advice,
        'metrics_4h':{k:h[k] for k in ('close','ema20','ema50','rsi14','atr14','atr_pct')},
        'four_hour_cutoff':four[-1]['t']+4*HOUR,'method':'closed-bars-v2; observations, not calibrated probabilities'}


def focus(rows,limit=8):
    """Always review market context even when no entry candidate exists."""
    result=[];used=set()
    for symbol in ANCHORS:
        row=next((r for r in rows if r['symbol']==symbol),None)
        if row is not None:result.append(row);used.add(row['market_id'])
    def rank(row):
        s=row.get('scenario',{})
        return (row.get('side')=='wait',not s.get('code','').startswith('reversal_'),-row.get('volume_24h_quote',0),row['market_id'])
    for row in sorted(rows,key=rank):
        if row['market_id'] not in used:result.append(row);used.add(row['market_id'])
        if len(result)>=limit:break
    return result[:limit]
