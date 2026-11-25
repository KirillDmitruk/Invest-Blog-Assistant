"""Public Lighter history. No wallet credentials, order endpoints or symbol aliases."""
from datetime import datetime, timezone
import json
import math
import time
import requests

BASE='https://mainnet.zklighter.elliot.ai/api/v1'
HOUR=3600
STRATEGY='trend-hourly-v1'


def finite(value,positive=False):
    if isinstance(value,bool):raise ValueError('Неверное числовое значение истории.')
    try:value=float(value)
    except (TypeError,ValueError,OverflowError):raise ValueError('Неверное числовое значение истории.') from None
    if not math.isfinite(value) or (positive and value<=0):raise ValueError('Неверное числовое значение истории.')
    return value


def iso(stamp):return datetime.fromtimestamp(stamp,timezone.utc).isoformat()


def parse_candles(data,cutoff):
    if not isinstance(data,dict) or data.get('code')!=200 or data.get('r')!='1h' or not isinstance(data.get('c'),list):
        raise ValueError('Lighter не подтвердил часовые свечи.')
    unique={}
    for raw in data['c']:
        if not isinstance(raw,dict):raise ValueError('Неверная запись свечи Lighter.')
        stamp=finite(raw.get('t'))/1000
        if stamp!=int(stamp) or stamp%HOUR or stamp>cutoff+HOUR:raise ValueError('Неверное время свечи Lighter.')
        if stamp+HOUR>cutoff:continue  # An unfinished bar never enters indicators or a test.
        bar={'t':int(stamp),**{k:finite(raw.get(k),True) for k in ('o','h','l','c')},
             'v':finite(raw.get('v',0)),'V':finite(raw.get('V',0))}
        if min(bar['v'],bar['V'])<0 or bar['l']>min(bar['o'],bar['c']) or bar['h']<max(bar['o'],bar['c']) or bar['h']<bar['l']:
            raise ValueError('Неверная структура OHLCV Lighter.')
        if bar['t'] in unique and unique[bar['t']]!=bar:raise ValueError('Противоречивые дубли свечей Lighter.')
        unique[bar['t']]=bar
    return sorted(unique.values(),key=lambda b:b['t'])[-480:]


class LighterHistory:
    def __init__(self,store,session=None,clock=time.time,sleep=time.sleep):
        self.store=store;self.session=session or requests.Session();self.clock=clock;self.sleep=sleep
        self.last_request=0.;self.markets=None;self.unavailable=False
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS walt_history_cache(market_id INTEGER PRIMARY KEY,cutoff INTEGER,payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_market_observations(contract_id INTEGER,bucket INTEGER,payload TEXT,PRIMARY KEY(contract_id,bucket));''')

    def get(self,path,params=None):
        if path not in ('orderBooks','candles'):raise ValueError('История разрешает только два публичных чтения Lighter.')
        if self.unavailable:raise ValueError('Источник истории временно недоступен; повтори проверку позже.')
        delay=1.3-(self.clock()-self.last_request)
        if delay>0:self.sleep(delay)  # Under 47 public requests/minute; no retry loops.
        self.last_request=self.clock()
        try:
            with self.session.get(BASE+'/'+path,params=params,timeout=(5,15),allow_redirects=False,stream=True) as r:
                if r.status_code==429:
                    self.unavailable=True
                    raise ValueError('Lighter ограничил частоту запросов. Уже полученная история сохранена; повтори позже.')
                if r.status_code!=200:
                    if r.status_code in (401,403,404) or r.status_code>=500:self.unavailable=True
                    raise ValueError('Lighter не подтвердил историю. Торговое решение пропущено.')
                parts=[];size=0
                for chunk in r.iter_content(65536):
                    size+=len(chunk)
                    if size>2_000_000:raise ValueError('Ответ истории превышает допустимый размер.')
                    parts.append(chunk)
                data=json.loads(b''.join(parts))
                if not isinstance(data,dict) or data.get('code')!=200:raise ValueError('Неожиданный ответ истории Lighter.')
                return data
        except requests.RequestException:
            self.unavailable=True
            raise ValueError('Не удалось прочитать Lighter; история недоступна. Автоповтора нет.') from None
        except (json.JSONDecodeError,UnicodeDecodeError):raise ValueError('Неверный формат истории Lighter.') from None

    def market(self,contract):
        if self.markets is None:
            raw=self.get('orderBooks').get('order_books')
            if not isinstance(raw,list):raise ValueError('Lighter не вернул каталог рынков.')
            self.markets={}
            for m in raw:
                if not isinstance(m,dict):raise ValueError('Неверная запись рынка Lighter.')
                if m.get('market_type')!='perp':continue
                ident=m.get('market_id')
                if type(ident) is not int or ident in self.markets:raise ValueError('Неоднозначные идентификаторы Lighter.')
                self.markets[ident]=m
        market=self.markets.get(contract['contract_id'])
        if not market or market.get('symbol')!=contract['base_asset'] or market.get('status')!='active' or market.get('is_frozen') is not False or finite(market.get('multiplier'))!=1:
            raise ValueError('Точное соответствие ID, тикера и множителя Walt ↔ Lighter не подтверждено.')
        return market

    def load(self,contract):
        market=self.market(contract);cutoff=int(self.clock())//HOUR*HOUR
        with self.store.db() as db:cached=db.execute('SELECT * FROM walt_history_cache WHERE market_id=?',(market['market_id'],)).fetchone()
        cached_ok=cached is not None and cached['cutoff']==cutoff
        if cached_ok:bars=json.loads(cached['payload'])
        else:
            raw=self.get('candles',{'market_id':market['market_id'],'resolution':'1h','start_timestamp':cutoff-480*HOUR,
                'end_timestamp':cutoff-1,'count_back':480,'set_timestamp_to_end':'false'})
            bars=parse_candles(raw,cutoff)
            with self.store.db() as db:db.execute('INSERT OR REPLACE INTO walt_history_cache VALUES(?,?,?)',(market['market_id'],cutoff,json.dumps(bars)))
        if len(bars)<240:raise ValueError(f'Недостаточно истории: {len(bars)} закрытых часов, нужно минимум 240.')
        if bars[-1]['t']+HOUR!=cutoff:raise ValueError('Последняя закрытая часовая свеча устарела; рынок может не торговаться.')
        if any(b['t']-a['t']!=HOUR for a,b in zip(bars,bars[1:])):raise ValueError('В истории есть пропуски часов. Стратегия для непрерывного ряда не применяется.')
        return {'bars':bars,'source':'Lighter','market_id':market['market_id'],'symbol':market['symbol'],
            'resolution':'1h','cutoff':cutoff,'cached':cached_ok,'from':iso(bars[0]['t']),'to':iso(bars[-1]['t']+HOUR)}

    def observation(self,row):
        stamp=int(row['observed']);bucket=stamp//60*60
        with self.store.db() as db:
            db.execute('INSERT OR IGNORE INTO walt_market_observations VALUES(?,?,?)',(row['contract_id'],bucket,json.dumps(row)))
            previous=db.execute('SELECT bucket,payload FROM walt_market_observations WHERE contract_id=? AND bucket BETWEEN ? AND ? ORDER BY ABS(bucket-?) LIMIT 1',
                (row['contract_id'],bucket-24*HOUR-1800,bucket-24*HOUR+1800,bucket-24*HOUR)).fetchone()
        if not previous:return None
        old=json.loads(previous['payload']);start=finite(old.get('open_interest_usd'),True);end=finite(row.get('open_interest_usd'),True)
        return {'change_pct':(end/start-1)*100,'hours':(bucket-previous['bucket'])/HOUR,'unit':'USD','source':'Walt · сохранённые наблюдения'}


def indicator_series(bars):
    output=[];ema20=ema50=bars[0]['c'];gain=loss=atr=0.
    for i,b in enumerate(bars):
        close=b['c'];prev=bars[i-1]['c'] if i else close
        ema20+=(close-ema20)*2/21;ema50+=(close-ema50)*2/51
        up=max(0.,close-prev);down=max(0.,prev-close)
        tr=max(b['h']-b['l'],abs(b['h']-prev),abs(b['l']-prev))
        if i<=14:
            gain+=up/14;loss+=down/14
            if i:atr+=tr/14
        else:
            gain=(gain*13+up)/14;loss=(loss*13+down)/14;atr=(atr*13+tr)/14
        rsi=50. if gain==loss==0 else 100. if loss==0 else 100-100/(1+gain/loss)
        output.append({'close':close,'ema20':ema20,'ema50':ema50,'rsi14':rsi,'atr14':atr,'atr_pct':atr/close*100,
            'change_24h_pct':(close/bars[i-24]['c']-1)*100 if i>=24 else None,
            'change_7d_pct':(close/bars[i-168]['c']-1)*100 if i>=168 else None})
    return output


def technical_side(m):
    if m['change_7d_pct'] is None:return 'wait'
    if m['close']>m['ema20']>m['ema50'] and m['change_24h_pct']>0 and m['change_7d_pct']>0 and 45<=m['rsi14']<=68:return 'long'
    if m['close']<m['ema20']<m['ema50'] and m['change_24h_pct']<0 and m['change_7d_pct']<0 and 32<=m['rsi14']<=55:return 'short'
    return 'wait'


def backtest(bars,series,stop_pct,take_pct,fee,slip):
    """Fixed rules, next-bar entry, adverse fills, stop first if both levels hit.

    No tuning and no live funding/OI substituted for their missing history.
    Results are R per trade, not returns of a funded or leveraged account.
    """
    trades=[];i=239;open_at_end=0
    while i<len(bars)-1:
        side=technical_side(series[i]);m=series[i]
        if side=='wait' or abs(m['change_24h_pct'])>=10 or m['atr_pct']*1.25>stop_pct:i+=1;continue
        direction=1 if side=='long' else -1
        first=i+1;reference=bars[first]['o'];entry=reference*(1+direction*slip)
        stop=reference*(1-direction*stop_pct/100);take=reference*(1+direction*take_pct/100)
        risk=abs(entry-stop*(1-direction*slip))+fee*(entry+stop*(1-direction*slip))
        exit_price=None;exit_index=None
        for j in range(first,min(first+48,len(bars))):
            b=bars[j]
            stopped=(b['l']<=stop if direction==1 else b['h']>=stop)
            taken=(b['h']>=take if direction==1 else b['l']<=take)
            if stopped:
                reference_exit=min(stop,b['o']) if direction==1 else max(stop,b['o'])
                exit_price=reference_exit*(1-direction*slip);exit_index=j;break
            if taken:exit_price=take*(1-direction*slip);exit_index=j;break
            if j==first+47:exit_price=b['c']*(1-direction*slip);exit_index=j;break
        if exit_price is None:open_at_end=1;break
        pnl=(exit_price-entry)*direction-fee*(entry+exit_price)
        trades.append(pnl/risk);i=exit_index+1
    positive=sum(x for x in trades if x>0);negative=-sum(x for x in trades if x<0)
    equity=peak=drawdown=0.
    for value in trades:
        equity+=value;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
    return {'trades':len(trades),'win_rate_pct':100*sum(x>0 for x in trades)/len(trades) if trades else None,
        'average_r':sum(trades)/len(trades) if trades else None,'total_r':sum(trades),'max_drawdown_r':drawdown,
        'profit_factor':positive/negative if negative else None,'open_at_end':open_at_end,
        'funding_included':False,'independent_validation':False,'from':iso(bars[240]['t']) if len(bars)>240 else None,
        'to':iso(bars[-1]['t']+HOUR),'limitations':'Короткая ретроспектива техправил с комиссиями и проскальзыванием. Без исторического фандинга, OI, исполнения Walt и доказательства будущей прибыли.'}


def assess(history,row,contract,limits,oi=None):
    bars=history['bars'];series=indicator_series(bars);m=series[-1]
    current=finite(row.get('price'),True);fee=finite(contract['taker_fee_pct']);slip=finite(contract['price_slippage'])
    if not 0<=fee<.1 or not 0<=slip<.1:raise ValueError('Неверные параметры расходов Walt.')
    stop=finite(limits['stop_pct'],True);take=finite(limits['take_pct'],True)
    side=technical_side(m);checks=[]
    def check(key,ok,text):checks.append({'key':key,'ok':bool(ok),'text':text})
    deviation=abs(current/m['close']-1)*100
    age=finite(row['observed'])-finite(history['cutoff'])
    check('history_fresh',0<=age<=5400,'Последний закрытый час истории должен быть не старше 90 минут и не из будущего.')
    check('price_match',deviation<=min(5.,max(2.,2*m['atr_pct'])),f'Расхождение Walt и закрытого часа Lighter: {deviation:.2f}%.')
    check('trend',side!='wait',f'Сутки {m["change_24h_pct"]:+.2f}%, неделя {m["change_7d_pct"]:+.2f}%; RSI {m["rsi14"]:.1f}. EMA и направления должны совпасть.')
    check('daily_move',abs(m['change_24h_pct'])<10,'Суточное изменение по модулю должно быть меньше 10%.')
    check('volatility',m['atr_pct']*1.25<=stop,f'ATR {m["atr_pct"]:.2f}%: стоп должен быть не уже 1,25 ATR.')
    check('liquidity',finite(row.get('volume_24h_usd',0))>=1_000_000,'Объём Walt за 24ч — не менее 1 млн USD.')
    direction=-1 if side=='short' else 1
    funding=finite(row.get('funding_rate_hourly'))*direction*24*100
    check('funding',side=='wait' or funding<=.5,f'Текущий фандинг × 24ч: {funding:+.3f}% номинала для выбранной стороны; это оценка при неизменной ставке.')
    check('venue',contract.get('venue_status')=='active' and not contract.get('trading_hours_state'), 'Открытие должно быть доступно по статусу контракта Walt.')
    # OI is evidence only when two comparable observations exist, never inferred from candles.
    bt=backtest(bars,series,stop,take,fee,slip)
    check('retrospective',bt['trades']<5 or bt['average_r']>0,'При пяти и более закрытых сделках отрицательная средняя R блокирует идею.')
    allowed=side!='wait' and all(c['ok'] for c in checks)
    failed=[c['text'] for c in checks if not c['ok']]
    reason=(f'Технический кандидат {side}: сутки {m["change_24h_pct"]:+.2f}%, неделя {m["change_7d_pct"]:+.2f}%, RSI {m["rsi14"]:.1f}.' if allowed else 'Ожидание. '+' '.join(failed))
    return {'strategy_version':STRATEGY,'data_verified':True,'rule_side':side if allowed else 'wait','reason':reason,
        'metrics':m,'checks':checks,'history':{k:v for k,v in history.items() if k!='bars'},'oi':oi,'backtest':bt,
        'risk_notice':'Экспериментальная стратегия, не подтверждённая доходность. История около 20 дней; новости не проверены. '+('Мало закрытых сделок для оценки.' if bt['trades']<20 else 'Ретроспектива не заменяет независимую проверку.')}


def review_candidates(agent,rows,progress=None):
    """Local model can accept/veto a computed candidate, never invent side or facts."""
    candidates=[r for r in rows if r.get('assessment',{}).get('rule_side') in ('long','short')]
    reviewed=0
    for offset in range(0,len(candidates),8):
        batch=candidates[offset:offset+8];ids=[r['contract_id'] for r in batch]
        if progress:progress(offset,len(candidates))
        schema={'type':'object','properties':{'reviews':{'type':'array','maxItems':len(batch),'items':{'type':'object','properties':{
            'contract_id':{'type':'integer','enum':ids},'decision':{'type':'string','enum':['review','wait']},
            'reason':{'type':'string','enum':['checks_pass','trend_unclear','funding_cost','volatility','data_uncertainty']}},
            'required':['contract_id','decision','reason'],'additionalProperties':False}}},'required':['reviews'],'additionalProperties':False}
        try:
            result=agent.local_request('/api/chat',{'model':agent.state()['model'],'stream':False,'think':False,'format':schema,
                'messages':[{'role':'system','content':'Ты проверяешь экспериментальные кандидаты детерминированной стратегии. '
                    'Не выбирай новый инструмент или направление. review = можно показать кандидата пользователю для ручного рассмотрения; wait = воздержаться. '
                    'Оцени текущий рынок: тренд суток/недели, EMA, RSI, ATR, ликвидность, текущий фандинг, качество данных. '
                    'Личные сделки пользователя не используются. Ретроспектива стратегии проверяется отдельно и не передаётся тебе. '
                    'Нельзя отказывать из-за количества личных или смоделированных сделок. Новости не подключены: не придумывай их, '
                    'это ограничение анализа, не самостоятельная причина wait. Не обещай прибыль. '
                    'Для review укажи checks_pass; для wait укажи причину по текущим данным: trend_unclear/funding_cost/volatility/data_uncertainty. '
                    'JSON reviews: contract_id,decision,reason.'},
                    {'role':'user','content':json.dumps([{'contract_id':r['contract_id'],'symbol':r.get('symbol'),
                        'side':r['assessment']['rule_side'],'metrics':r['assessment']['metrics'],
                        'volume_24h_usd':r.get('volume_24h_usd'),'funding_rate_hourly':r.get('funding_rate_hourly'),
                        'oi_observation':r['assessment'].get('oi'),
                        'market_checks':[c for c in r['assessment'].get('checks',[]) if c['key']!='retrospective']}
                        for r in batch],ensure_ascii=False)+' /no_think'}],
                'options':{'temperature':0,'num_ctx':8192,'num_predict':600}})
            values=json.loads(result['message']['content'])['reviews'];accepted={}
            for value in values:
                ident=value.get('contract_id');decision=value.get('decision')
                reason=value.get('reason')
                if type(ident) is not int or ident not in ids or ident in accepted or decision not in ('review','wait') or reason not in ('checks_pass','trend_unclear','funding_cost','volatility','data_uncertainty'):raise ValueError
                if (decision=='review')!=(reason=='checks_pass'):raise ValueError
                accepted[ident]=(decision,reason)
            if len(accepted)!=len(batch):raise ValueError
            for row in batch:
                a=row['assessment'];decision,reason=accepted[row['contract_id']]
                text={'checks_pass':'Проверка модели завершена; это не доказательство прибыльности.',
                      'trend_unclear':'Модель считает текущий сетап тренда и индикаторов неоднозначным.',
                      'funding_cost':'Модель выбрала ожидание из-за оценки текущих расходов на удержание.',
                      'volatility':'Модель выбрала ожидание из-за текущей волатильности.',
                      'data_uncertainty':'Модель считает текущих рыночных подтверждений недостаточно.'}[reason]
                row['model_review']={'status':'complete','decision':decision,'reason':text}
                row['ai']={'side':a['rule_side'] if decision=='review' else 'wait','reason':a['reason'] if decision=='review' else 'Локальный ИИ: ожидание. '+text}
                reviewed+=1
        except Exception:
            for row in batch:
                row['model_review']={'status':'unavailable','decision':'wait'}
                row['ai']={'side':'wait','reason':'Технические показатели рассчитаны, но проверка локальной моделью не подтверждена. Посмотри расчёт стратегии.'}
    return reviewed
