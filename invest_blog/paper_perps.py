"""Autonomous dollar PAPER ledger. Pure simulation: no transport or broker tools."""
from decimal import Decimal,ROUND_FLOOR
import copy,hashlib,math

def num(x):
    if isinstance(x,bool):raise ValueError('Invalid number')
    d=Decimal(str(x))
    if not d.is_finite():raise ValueError('Invalid number')
    return d

def amount(x):return format(num(x),'f')

def activate_v2(state,now):
    if state.get('strategy_version')=='walt-v2-20261125':return False
    baseline={'at':now,'equity':state['equity'],'cash':state['cash'],'closed_count':len(state['closed'])}
    state.update(strategy_version='walt-v2-20261125',strategy_baseline=baseline)
    state.setdefault('strategy_changes',[]).append({'version':'walt-v2-20261125',**baseline})
    state['limits'].update(max_same_side=2,direction_risk_pct='2.5')
    return True

def initial():
    return {'version':1,'mode':'paper','currency':'USD','initial_balance':'10000','cash':'10000',
        'positions':{},'closed':[],'events':[],'seen':[],'last_cycle':None,'last_report':None,
        'equity':'10000','unrealized':'0','high_water':'10000','day':'','day_start':'10000','paused':False,
        'reason':'Ожидаю первый свежий анализ. Реальные сделки отключены.',
        'limits':{'leverage':3,'risk_pct':'1','max_positions':4,'group_positions':2,'max_margin_pct':'25',
            'portfolio_risk_pct':'5','stop_pct':'3.5','take_pct':'10','slippage':'.0005','fee_rate':'.0007',
            'daily_loss_pct':'3','drawdown_pct':'10'},'notifications':{}}

def group(symbol):
    if symbol in ('NVDA','AAPL','MSFT','AMD','GOOGL','GOOG','META','AMZN','TSLA','AVGO','INTC'):return 'technology'
    if symbol in ('XAU','XAG','XCU','GOLD','SILVER','OIL','BRENT','WTI'):return 'commodities'
    currencies={'USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD','CNH','KRW','MXN'}
    if len(symbol)==6 and symbol[:3] in currencies and symbol[3:] in currencies:return 'forex'
    return 'other' # Includes crypto; deliberately one capped bucket, no inferred sectors.

class PaperEngine:
    def __init__(self,state):
        self.state=copy.deepcopy(state);self.changes=[]
        if state.get('version')!=1 or state.get('mode')!='paper' or num(state['initial_balance'])!=10000:raise ValueError('Wrong paper state')
    def event(self,kind,p,at,extra=None):
        e={'id':hashlib.sha256(f'{kind}:{p["id"]}:{at}'.encode()).hexdigest()[:20],
            'kind':kind,'symbol':p['symbol'],'side':p['side'],'at':at,**(extra or {})}
        self.state['events'].append(e);self.state['events']=self.state['events'][-300:];self.changes.append(e)
    def values(self):
        s=self.state
        unreal=sum((num(p['quantity'])*(num(p['mark'])-num(p['entry']))*(1 if p['side']=='long' else -1) for p in s['positions'].values()),Decimal(0))
        equity=num(s['cash'])+unreal;reserved=sum((num(p['margin']) for p in s['positions'].values()),Decimal(0))
        s.update(equity=amount(equity),unrealized=amount(unreal),reserved=amount(reserved),free=amount(equity-reserved))
        s['high_water']=amount(max(num(s['high_water']),equity))
        return equity,equity-reserved
    def close(self,key,price,at,reason):
        s=self.state;p=s['positions'][key];direction=1 if p['side']=='long' else -1
        fill=num(price)*(1-direction*num(s['limits']['slippage']));qty=num(p['quantity'])
        gross=qty*(fill-num(p['entry']))*direction
        # Isolated simulation: adverse gaps cannot consume other virtual margin.
        gross=max(gross,-num(p['margin']))
        fee=qty*fill*num(s['limits']['fee_rate']);s['cash']=amount(num(s['cash'])+gross-fee)
        net=gross-fee-num(p['entry_fee'])-num(p['funding_paid'])
        trade={**p,'exit':amount(fill),'closed_at':at,'reason':reason,'net_pnl':amount(net),'exit_fee':amount(fee)}
        s['closed'].append(trade);s['closed']=s['closed'][-300:]
        del s['positions'][key];self.event('close',p,at,{'reason':reason,'pnl':amount(net)})
    def monitor(self,key,observation,now):
        s=self.state;p=s['positions'][key];direction=1 if p['side']=='long' else -1
        if observation['symbol']!=p['symbol']:raise ValueError('Symbol changed')
        quote=num(observation['price'])
        if quote<=0 or not -120<=now-observation['at']<=120:raise ValueError('Stale quote')
        bars=sorted(observation['bars'],key=lambda b:b['t'])
        # Ignore the partially elapsed entry minute: its earlier high/low predates entry.
        start=max(int(p['opened_at']//60+1)*60,int(p['checked_until']))
        bars=[b for b in bars if start<=b['t'] and b['t']+60<=now]
        if start<=now//60*60-60:
            expected=list(range(start,int(now//60*60),60))
            if [b['t'] for b in bars]!=expected:raise ValueError('Minute history gap; virtual monitoring paused')
        fundings=sorted(observation['fundings'],key=lambda f:f['t'])
        def accrue(until):
            for f in fundings:
                if p['funding_until']<f['t']<=until and f['t']>p['opened_at']:
                    value=num(f['value'])*num(p['quantity'])*(1 if f['direction']==p['side'] else -1)
                    s['cash']=amount(num(s['cash'])-value);p['funding_paid']=amount(num(p['funding_paid'])+value)
                    p['funding_until']=f['t']
        for b in bars:
            o,h,l,c=(num(b[k]) for k in ('o','h','l','c'))
            if not 0<l<=min(o,c)<=max(o,c)<=h:raise ValueError('Malformed OHLC')
            accrue(b['t'])
            stop=num(p['stop']);take=num(p['take'])
            hit_stop=l<=stop if direction==1 else h>=stop
            hit_take=h>=take if direction==1 else l<=take
            if hit_stop: # Stop wins when both levels occur in one minute.
                self.close(key,min(stop,o) if direction==1 else max(stop,o),b['t'],'stop');return
            if hit_take:
                self.close(key,take,b['t'],'take');return
            p['checked_until']=b['t']+60
        accrue(now)
        p['mark']=amount(quote);p['marked_at']=observation['at'];p.pop('data_error',None)
        if direction*(quote-num(p['stop']))<=0:self.close(key,quote,now,'stop_quote');return
        if direction*(quote-num(p['take']))>=0:self.close(key,quote,now,'take_quote');return
        if fundings:
            latest=fundings[-1]
            p['funding_reserve']=amount(num(latest['value'])*num(p['quantity'])*24 if latest['direction']==p['side'] else 0)
        stop_fill=num(p['stop'])*(1-direction*num(s['limits']['slippage']))
        p['risk']=amount(max(Decimal(0),num(p['quantity'])*(quote-stop_fill)*direction)
            +num(p['quantity'])*(quote+stop_fill)*num(s['limits']['fee_rate'])+num(p.get('funding_reserve','0')))
        p['maintenance']=observation['maintenance']
        equity=num(p['margin'])+num(p['quantity'])*(quote-num(p['entry']))*direction-num(p['funding_paid'])
        if equity<=num(p['quantity'])*quote*num(p['maintenance']):self.close(key,quote,now,'liquidation_model')
    def open(self,row,quote,report_id,now):
        s=self.state;limits=s['limits'];key=str(row['market_id']);side=row['side'];symbol=row['symbol']
        if key in s['positions'] or len(s['positions'])>=limits['max_positions']:return False
        same_side=[p for p in s['positions'].values() if p['side']==side]
        if len(same_side)>=limits.get('max_same_side',limits['max_positions']):return False
        if sum(p['group']==group(symbol) for p in s['positions'].values())>=limits['group_positions']:return False
        if quote['symbol']!=symbol or quote.get('active') is not True or not -120<=now-quote['at']<=120:raise ValueError('Invalid entry quote')
        now=max(now,quote['at'])
        price=num(quote['price']);step=num(quote['step']);leverage=min(limits['leverage'],int(quote['max_leverage']))
        if price<=0 or step<=0 or leverage<1:raise ValueError('Invalid contract')
        direction=1 if side=='long' else -1;entry=price*(1+direction*num(limits['slippage']))
        stop=entry*(1-direction*num(limits['stop_pct'])/100);take=entry*(1+direction*num(limits['take_pct'])/100)
        equity,free=self.values();risk_cap=equity*num(limits['risk_pct'])/100
        held_risk=sum((num(p['risk']) for p in s['positions'].values()),Decimal(0))
        risk_cap=min(risk_cap,equity*num(limits['portfolio_risk_pct'])/100-held_risk)
        if 'direction_risk_pct' in limits:
            risk_cap=min(risk_cap,equity*num(limits['direction_risk_pct'])/100-sum((num(p['risk']) for p in same_side),Decimal(0)))
        stop_fill=stop*(1-direction*num(limits['slippage']))
        funding_reserve=num(quote.get('hour_funding_value','0'))*24 if quote.get('funding_payer')==side else Decimal(0)
        per_unit=abs(entry-stop_fill)+(entry+stop_fill)*num(limits['fee_rate'])+funding_reserve
        qty=(min(risk_cap/per_unit,free*leverage/entry/(1+leverage*num(limits['fee_rate'])),
            equity*num(limits['max_margin_pct'])/100*leverage/entry)/step).to_integral_value(rounding=ROUND_FLOOR)*step
        if qty<num(quote['min_quantity']) or qty*entry<num(quote['min_notional']) or qty<=0:return False
        margin=qty*entry/leverage;fee=qty*entry*num(limits['fee_rate'])
        if margin+fee>free:return False
        ident=hashlib.sha256(f'{report_id}:{key}:{side}'.encode()).hexdigest()[:20]
        p={'id':ident,'market_id':row['market_id'],'symbol':symbol,'group':group(symbol),'side':side,'leverage':leverage,
            'quantity':amount(qty),'entry':amount(entry),'mark':amount(price),'margin':amount(margin),'entry_fee':amount(fee),
            'stop':amount(stop),'take':amount(take),'risk':amount(qty*per_unit),'funding_reserve':amount(qty*funding_reserve),'funding_paid':'0','maintenance':quote['maintenance'],
            'opened_at':now,'marked_at':now,'checked_until':int(now//60+1)*60,'funding_until':now,'source_report':report_id}
        s['cash']=amount(num(s['cash'])-fee);s['positions'][key]=p;self.event('open',p,now,{'margin':p['margin'],'entry':p['entry']});return True
    def cycle(self,packet,market,now):
        s=self.state;warnings=[];closed_markets=set()
        for key in list(s['positions']):
            try:
                observed=market.observe(int(key),min(s['positions'][key]['checked_until'],s['positions'][key]['funding_until']),now)
                self.monitor(key,observed,now)
                if key not in s['positions']:closed_markets.add(key)
            except Exception:
                if key in s['positions']:s['positions'][key]['data_error']='Нет полной свежей истории; позиция не переигрывается задним числом.'
                warnings.append('Не удалось проверить '+s['positions'].get(key,{}).get('symbol',key))
        equity,free=self.values();day=int(now+3*3600)//86400 # Moscow trading day.
        if s['day']!=day:
            s['day']=day;s['day_start']=amount(equity)
            if s.get('pause_reason')=='daily_loss':s['paused']=False;s.pop('pause_reason',None)
        daily=(num(s['day_start'])-equity)/num(s['day_start'])*100 if num(s['day_start'])>0 else Decimal(100)
        draw=(num(s['high_water'])-equity)/num(s['high_water'])*100
        if draw>=num(s['limits']['drawdown_pct']):s['paused']=True;s['pause_reason']='drawdown'
        elif daily>=num(s['limits']['daily_loss_pct']):s['paused']=True;s['pause_reason']='daily_loss'
        reason='Новых подтверждённых ИИ-сигналов нет.';eligible=0;opened=0
        if s['paused']:reason='Новые входы приостановлены лимитом убытка; виртуальные стопы продолжают проверяться.'
        elif warnings:reason='Новые входы приостановлены: не все текущие позиции удалось проверить.'
        elif packet and packet.get('ai',{}).get('status')=='ok' and 0<=now-packet['created_epoch']<=10800:
            report_id=packet['created_at'];s['last_report']=report_id
            s['last_scan']={'checked':packet.get('checked'),'total':packet.get('total'),'ai_reviewed':packet['ai'].get('reviewed'),'at':report_id}
            for row in packet['rows']:
                if row.get('side') not in ('long','short') or row.get('ai_review',{}).get('accept') is not True or row.get('entry_blockers'):continue
                eligible+=1;signal=report_id+':'+str(row['market_id'])
                if signal in s['seen'] or str(row['market_id']) in closed_markets:continue
                try:
                    if self.open(row,market.entry(row['market_id'],now),report_id,now):opened+=1;s['seen'].append(signal)
                except Exception:warnings.append('Пропущен '+row['symbol']+': не подтверждены свежие данные.')
            if eligible:reason=f'Подтверждённых кандидатов: {eligible}. Новых виртуальных входов: {opened}.'
        else:reason='Ожидаю свежий отчёт с успешной проверкой ИИ; старые сигналы не исполняются.'
        s.update(last_cycle=now,reason=reason,warnings=warnings,eligible=eligible);s['seen']=s['seen'][-1000:]
        self.values();return s
