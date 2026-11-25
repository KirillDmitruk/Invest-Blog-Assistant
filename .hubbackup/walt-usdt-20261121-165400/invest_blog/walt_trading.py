"""Signal -> deterministic sizing -> provider preview -> explicit user execution.

The model never receives a trade token or access to execution tools.
"""
from datetime import datetime,timezone
from contextlib import contextmanager
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING,InvalidOperation
import hashlib
import json
import secrets
import time

from .walt_agent import WaltAgent,now


def number(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite():raise ValueError
        return result
    except (ValueError,TypeError,InvalidOperation):raise ValueError('Walt вернул неподтверждённое числовое значение.') from None


def decimal(value):return format(value,'f')


def unpack(result):
    if not isinstance(result,dict) or result.get('isError'):raise ValueError('Walt не подтвердил операцию. Проверь подключение и состояние счёта.')
    if isinstance(result.get('structuredContent'),dict):return result['structuredContent']
    for item in result.get('content',[]):
        if item.get('type')=='text':
            try:
                value=json.loads(item['text'])
                if isinstance(value,dict):return value
            except (ValueError,TypeError):pass
    raise ValueError('Формат ответа Walt изменился; операция остановлена для проверки.')


def rounded(price,tick,up=False):
    return (price/tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*tick


def expiry(value):
    if isinstance(value,(int,float)) or isinstance(value,str) and value.replace('.','',1).isdigit():
        stamp=float(value)
        return stamp/1000 if stamp>100000000000 else stamp
    if isinstance(value,str):
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo:return stamp.timestamp()
    raise ValueError('Walt не подтвердил срок одноразового предпросмотра.')


def group_for(symbol):
    if symbol in ('NVDA','AAPL','MSFT','AMD','GOOGL','GOOG','META','AMZN','TSLA','AVGO','INTC'):return 'technology'
    if symbol in ('XAU','XAG','GOLD','SILVER','OIL','BRENT','WTI'):return 'commodities'
    return 'other'  # Conservative shared bucket for unclassified assets, not an inferred industry.


DEFAULT_LIMITS={'revision':1,'leverage':5,'risk_pct':'1','portfolio_risk_pct':'5',
    'margin_free_pct':'25','margin_equity_pct':'35','min_margin':'2','max_margin':'10',
    'stop_pct':'3.5','take_pct':'10','max_positions':4,'max_group_positions':2,'max_drift_pct':'0.5'}


def calculate(contract,price,free,equity,limits,side,existing_risk=Decimal(0),requested=None,fixed=None):
    if side not in ('long','short'):raise ValueError('Выбери лонг или шорт.')
    price,free,equity=number(price),number(free),number(equity)
    tick=number(contract.get('tick_size'));step=number(contract.get('step_size'))
    fee=number(contract.get('taker_fee_pct'));slip=number(contract.get('price_slippage'))
    if price<=0 or tick<=0 or step<=0 or free<=0 or equity<=0 or fee<0 or not 0<=slip<Decimal('.1'):
        raise ValueError('Недостаточно подтверждённых данных цены, счёта, комиссии или проскальзывания.')
    leverage=min(int(limits['leverage']),int(contract['max_leverage']))
    if not 1<=leverage<=5:raise ValueError('Допустимое плечо — от 1× до 5×.')
    direction=1 if side=='long' else -1
    entry=rounded(price*(1+direction*slip),tick,side=='long')
    stop=number(fixed['stop']) if fixed else rounded(price*(1-direction*number(limits['stop_pct'])/100),tick,side=='short')
    take=number(fixed['take']) if fixed else rounded(price*(1+direction*number(limits['take_pct'])/100),tick,side=='short')
    buffer=number(contract.get('tpsl_buffer_pct','0'))
    if min(entry,stop,take)<=0 or direction*(price-stop)<=price*buffer or direction*(take-price)<=price*buffer:
        raise ValueError('Стоп/тейк находятся с неверной стороны цены либо нарушают буфер Walt.')
    stop_fill=stop*(1-direction*slip)
    # Include adverse entry/exit slippage and fees; no promise that a gap respects this bound.
    loss_fraction=abs(entry-stop_fill)/entry+2*fee
    risk_cap=min(equity*number(limits['risk_pct'])/100,
                 equity*number(limits['portfolio_risk_pct'])/100-existing_risk)
    caps=[free*number(limits['margin_free_pct'])/100,equity*number(limits['margin_equity_pct'])/100,
          number(limits['max_margin']),number(contract['max_size_quote']),risk_cap*(1/Decimal(leverage)+fee)/loss_fraction]
    amount=min(caps).quantize(Decimal('.01'),rounding=ROUND_FLOOR)
    if requested is not None:
        requested=number(requested)
        if requested<=0 or requested>amount:raise ValueError('Запрошенная маржа превышает расчётный лимит. Сумма автоматически не увеличивается.')
        amount=requested.quantize(Decimal('.01'),rounding=ROUND_FLOOR)
    minimum=max(number(limits['min_margin']),number(contract['min_size_quote']))
    if amount<minimum:raise ValueError('Даже минимальная маржа не проходит лимит риска или свободного остатка. Сигнал пропущен.')
    notional=amount/(1/Decimal(leverage)+fee)
    quantity=(notional/entry/step).to_integral_value(rounding=ROUND_FLOOR)*step
    if quantity<=0 or quantity<number(contract['min_size_base']) or quantity>number(contract['max_size_base']):raise ValueError('Размер выходит за пределы количества контракта.')
    loss=quantity*abs(entry-stop_fill)+quantity*entry*fee+quantity*stop_fill*fee
    take_fill=take*(1-direction*slip)
    gain=quantity*(take_fill-entry)*direction-quantity*entry*fee-quantity*take_fill*fee
    if gain<=0 or loss>risk_cap:raise ValueError('Стоп/тейк и расходы не проходят ограничение риска.')
    funding=quantity*price*number(contract['funding_rate_hourly'])*direction
    return {'contract_id':contract['contract_id'],'symbol':contract['base_asset'],'currency':contract['quote_asset'],
        'side':side,'margin':decimal(amount),'leverage':leverage,'reference_price':decimal(price),
        'worst_entry':decimal(entry),'quantity_estimate':decimal(quantity),'notional_estimate':decimal(quantity*entry),
        'stop':decimal(stop),'take':decimal(take),'risk_estimate':decimal(loss.quantize(Decimal('.01'),rounding=ROUND_CEILING)),
        'profit_estimate':decimal(gain.quantize(Decimal('.01'),rounding=ROUND_FLOOR)),
        'funding_hour_estimate':decimal(funding.quantize(Decimal('.000001'))),'liquidation':None,
        'free_balance':decimal(free),'equity_estimate':decimal(equity),'group':group_for(contract['base_asset'])}


class WaltTrading:
    def __init__(self,agent):
        self.agent=agent
        with agent.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS walt_trade_limits(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_trade_signals(id TEXT PRIMARY KEY,at REAL,payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_trade_drafts(id TEXT PRIMARY KEY,at REAL,status TEXT,payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_trade_lock(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,expires REAL);''')
            db.execute('INSERT OR IGNORE INTO walt_trade_limits VALUES(1,?)',(json.dumps(DEFAULT_LIMITS),))

    def limits(self):
        with self.agent.db() as db:return json.loads(db.execute('SELECT payload FROM walt_trade_limits WHERE id=1').fetchone()[0])

    def save_limits(self,values,revision):
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE');current=json.loads(db.execute('SELECT payload FROM walt_trade_limits WHERE id=1').fetchone()[0])
            if revision!=current['revision']:raise ValueError('Лимиты изменились. Обнови страницу.')
            current.update(values,revision=revision+1)
            db.execute('UPDATE walt_trade_limits SET payload=? WHERE id=1',(json.dumps(current),))
        return current

    def read(self,name,args=None,sid=None):return unpack(self.agent.rpc('tools/call',{'name':name,'arguments':args or {}},sid)[0])

    def catalog(self,sid):
        rows=self.read('perps_list_contracts',sid=sid).get('contracts')
        if not isinstance(rows,list):raise ValueError('Walt не вернул каталог контрактов.')
        return {r['contract_id']:r for r in rows if r.get('is_active') is True and r.get('quote_asset')=='USDC'}

    def price(self,c,sid):
        response=self.read('perps_latest_rate',{'marketId':c['base_asset']+'-USD-PERP'},sid)
        value=number(response.get('rate'))
        if value<=0:raise ValueError('Walt не подтвердил цену.')
        return value

    def account(self,catalog,sid):
        balances=self.read('balance_get',sid=sid).get('accounts',[])
        accounts=[b for b in balances if str(b.get('currency','')).upper()=='USDC']
        if len(accounts)!=1:raise ValueError('Не подтверждён единственный свободный баланс USDC этого агентского счёта. Конвертация из USDT не выполняется.')
        free=number(accounts[0]['balance_available']);equity=free;risk=Decimal(0)
        positions=self.read('perps_list_positions',{'statuses':['open']},sid).get('positions')
        if not isinstance(positions,list):raise ValueError('Не удалось подтвердить открытые позиции.')
        for p in positions:
            c=catalog.get(p.get('contract_id'));side=p.get('side')
            if not c or side not in ('long','short'):raise ValueError('Неизвестный формат открытой позиции. Новые входы заблокированы.')
            quote=self.price(c,sid);qty=abs(number(p['value_base']));entry=number(p['avg_entry_price']);direction=1 if side=='long' else -1
            equity+=max(Decimal(0),number(p['margin_quote'])+qty*(quote-entry)*direction)
            try:
                stop=number(p['auto_close']['stop_loss']['trigger_price_quote']);liq=number(p['liquidation_price'])
            except (KeyError,TypeError):raise ValueError('У существующей позиции не подтверждён стоп/ликвидация. Новые входы заблокированы.') from None
            if direction*(stop-liq)<=0:raise ValueError('Стоп существующей позиции находится за ликвидацией. Проверь Walt.')
            risk+=max(Decimal(0),qty*(quote-stop)*direction)+qty*quote*(2*number(c['taker_fee_pct'])+number(c['price_slippage']))
        return {'free':free,'equity':equity,'risk':risk,'positions':positions}

    def signal(self):
        owner=secrets.token_hex(16)
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM walt_agent_lock WHERE id=1').fetchone()
            if row and row['expires']>time.time():raise ValueError('Локальная модель уже выполняет анализ. Дождись завершения.')
            db.execute('INSERT OR REPLACE INTO walt_agent_lock VALUES(1,?,?)',(owner,time.time()+900))
        try:return self._signal()
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_agent_lock WHERE owner=?',(owner,))

    def _signal(self):
        sid=self.agent.connect();catalog=self.catalog(sid)
        selected=sorted(catalog.values(),key=lambda c:number(c.get('volume_24h_usd') or 0),reverse=True)[:6]
        for symbol in ('BTC','ETH','NVDA'):
            found=next((c for c in catalog.values() if c['base_asset']==symbol),None)
            if found and found not in selected:selected.append(found)
        rows=[];errors=[]
        for c in selected:
            try:
                price=self.price(c,sid)
                rows.append({'contract_id':c['contract_id'],'symbol':c['base_asset'],'currency':c['quote_asset'],
                    'price':decimal(price),'volume_24h_usd':c.get('volume_24h_usd'),'open_interest_usd':c.get('open_interest_usd'),
                    'funding_rate_hourly':c.get('funding_rate_hourly'),'observed_at':now()})
            except ValueError:errors.append(c['base_asset'])
        if not rows:raise ValueError('Свежие котировки Walt недоступны.')
        ids=[r['contract_id'] for r in rows]
        schema={'type':'object','properties':{'signals':{'type':'array','maxItems':len(rows),'items':{'type':'object',
            'properties':{'contract_id':{'type':'integer','enum':ids},'side':{'type':'string','enum':['long','short','wait']},
                          'reason':{'type':'string'}},'required':['contract_id','side','reason'],'additionalProperties':False}}},
            'required':['signals'],'additionalProperties':False}
        data=self.agent.local_request('/api/chat',{'model':self.agent.state()['model'],'stream':False,'think':False,'format':schema,
            'messages':[{'role':'system','content':'Ты аналитик. Сравни только полученные инструменты Walt. Данные: последнее закрытие 1мин, объём, OI и текущий почасовой фандинг. '
                'Нет свечей за сутки/неделю, новостей или истории изменения OI. Не выдумывай тренд, процент роста, прогноз, сигнал по RSI. '
                'Один фандинг не достаточен для входа: при отсутствии подтверждения выбирай wait. Положительный фандинг: лонги платят шортам. '
                'Верни JSON signals: contract_id,side(long/short/wait),reason по-русски. Не задавай сумму, плечо или уровни, это считает код.'},
                {'role':'user','content':json.dumps(rows,ensure_ascii=False)+' /no_think'}],
            'options':{'temperature':0,'num_ctx':8192,'num_predict':1800}})
        try:proposals=json.loads(data['message']['content'])['signals']
        except (KeyError,TypeError,ValueError):raise ValueError('Модель не вернула проверенный список сигналов.') from None
        approved={}
        for p in proposals:
            if p.get('contract_id') not in ids or p.get('side') not in ('long','short','wait') or not isinstance(p.get('reason'),str):raise ValueError('Модель предложила неизвестный инструмент или действие.')
            if p['contract_id'] in approved:raise ValueError('Модель продублировала инструмент.')
            approved[p['contract_id']]=p
        for row in rows:row['ai']=approved.get(row['contract_id'],{'side':'wait','reason':'Модель не подтвердила вход.'})
        packet={'id':secrets.token_hex(16),'created':time.time(),'expires':time.time()+300,'candidates':rows,'errors':errors,
                'coverage':f'Проверено {len(rows)} из {len(catalog)} доступных контрактов. Выборка по объёму и BTC/ETH/NVDA; не полный рынок.',
                'limitations':'Дневной/недельный тренд, падение за сутки и изменение OI не проверены: в MCP нет нужной истории.'}
        with self.agent.db() as db:db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',(packet['id'],packet['created'],json.dumps(packet,ensure_ascii=False)))
        return packet

    def row(self,ident):
        with self.agent.db() as db:row=db.execute('SELECT * FROM walt_trade_drafts WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Сделка не найдена.')
        return dict(row),json.loads(row['payload'])

    def quote(self,signal_id,contract_id,side,requested=None):
        with self.agent.db() as db:row=db.execute('SELECT payload FROM walt_trade_signals WHERE id=?',(signal_id,)).fetchone()
        if not row:raise ValueError('Сначала нажми «Сигнал».')
        packet=json.loads(row[0])
        if time.time()>packet['expires'] or contract_id not in [r['contract_id'] for r in packet['candidates']]:raise ValueError('Сигнал устарел или инструмент не входит в выборку.')
        sid=self.agent.connect();catalog=self.catalog(sid);c=catalog.get(contract_id)
        if not c:raise ValueError('Инструмент больше недоступен в Walt.')
        account=self.account(catalog,sid);limits=self.limits()
        if len(account['positions'])>=limits['max_positions']:raise ValueError('Достигнут лимит открытых позиций.')
        if any(p.get('contract_id')==contract_id for p in account['positions']):raise ValueError('По этому контракту уже есть позиция. Наращивание не поддерживается.')
        groups=[group_for(catalog[p['contract_id']]['base_asset']) for p in account['positions']]
        if groups.count(group_for(c['base_asset']))>=limits['max_group_positions']:raise ValueError('Достигнут лимит позиций в одной локальной группе активов.')
        proposal=calculate(c,self.price(c,sid),account['free'],account['equity'],limits,side,account['risk'],requested)
        ident=secrets.token_hex(16)
        draft={'id':ident,'proposal':proposal,'contract':c,'created':time.time(),'expires':time.time()+300,
               'limits_revision':limits['revision'],'generation':self.agent.state()['generation'],
               'ai':next(r['ai'] for r in packet['candidates'] if r['contract_id']==contract_id),'warnings':[
                   'Реальные средства USDC. Рыночная заявка: цена исполнения не гарантируется.',
                   'Риск включает оценку комиссий и проскальзывания, но гэп и изменение фандинга могут увеличить убыток.',
                   'Цель 10% от цены и доходность 10% на маржу — разные показатели.']}
        with self.agent.db() as db:db.execute('INSERT INTO walt_trade_drafts VALUES(?,?,?,?)',(ident,time.time(),'calculated',json.dumps(draft,ensure_ascii=False)))
        return self.public(ident)

    def public(self,ident):
        row,draft=self.row(ident)
        def redact(value):
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if not any(word in k.lower() for word in ('token','secret','verifier'))}
            if isinstance(value,list):return [redact(v) for v in value]
            return value
        return redact({k:v for k,v in {**draft,'status':row['status']}.items() if k not in ('contract',)})

    def ensure_current(self,draft,sid):
        if draft['generation']!=self.agent.state()['generation']:raise ValueError('Подключение Walt изменилось. Рассчитай сделку заново.')
        if draft['limits_revision']!=self.limits()['revision']:raise ValueError('Лимиты изменились. Рассчитай сделку заново.')
        catalog=self.catalog(sid);c=catalog.get(draft['proposal']['contract_id'])
        if not c:raise ValueError('Контракт недоступен.')
        account=self.account(catalog,sid);p=draft['proposal'];price=self.price(c,sid);limits=self.limits()
        if min(int(limits['leverage']),int(c['max_leverage']))!=p['leverage']:raise ValueError('Допустимое плечо изменилось. Нужен новый расчёт.')
        if abs(price-number(p['reference_price']))/number(p['reference_price'])*100>number(limits['max_drift_pct']):raise ValueError('Цена изменилась сильнее допустимого. Нужен новый расчёт и подтверждение.')
        if len(account['positions'])>=limits['max_positions'] or any(pos.get('contract_id')==c['contract_id'] for pos in account['positions']):raise ValueError('Состав позиций изменился. Проверь кошелёк и пересчитай сделку.')
        if sum(group_for(catalog[pos['contract_id']]['base_asset'])==group_for(c['base_asset']) for pos in account['positions'])>=limits['max_group_positions']:raise ValueError('Достигнут лимит локальной группы активов.')
        calculate(c,price,account['free'],account['equity'],limits,p['side'],account['risk'],p['margin'],fixed=p)

    def prepare(self,ident):
        owner=secrets.token_hex(16)
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE')
            lock=db.execute('SELECT * FROM walt_trade_lock WHERE id=1').fetchone()
            if lock and lock['expires']>time.time():raise ValueError('Другая операция уже проверяется.')
            db.execute('INSERT OR REPLACE INTO walt_trade_lock VALUES(1,?,?)',(owner,time.time()+600))
        try:return self._prepare(ident)
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_trade_lock WHERE owner=?',(owner,))

    def _prepare(self,ident):
        row,draft=self.row(ident)
        if row['status'] not in ('calculated','previewed','blocked') or time.time()>draft['expires']:raise ValueError('Расчёт устарел или уже использован.')
        sid=self.agent.connect();self.ensure_current(draft,sid);p=draft['proposal']
        args={'contractId':p['contract_id'],'side':p['side'],'type':'market','leverage':p['leverage'],
              'amount':p['margin'],'price':p['reference_price'],'autoClose':{'takeProfit':{'priceQuote':p['take']},'stopLoss':{'priceQuote':p['stop']}}}
        value=unpack(self.agent.rpc('tools/call',{'name':'perps_prepare_order_place','arguments':args},sid,_manual=True)[0])
        if value.get('ok') is not True or not value.get('confirmationToken'):
            checks=value.get('risk',{}).get('checks',[])
            failures=[str(c.get('code') or c.get('name') or c.get('id') or '') for c in checks if isinstance(c,dict) and (c.get('ok') is False or c.get('passed') is False)]
            raise ValueError('Walt отклонил предварительную проверку. Исполнения не было. '+', '.join(failures))
        action=value.get('action');risk=value.get('risk')
        # Match the provider's actual action echo before allowing the user to execute.
        def find_params(obj):
            if isinstance(obj,dict):
                if all(k in obj for k in ('contractId','side','amount','leverage','autoClose')):return obj
                for child in obj.values():
                    found=find_params(child)
                    if found:return found
            return None
        echoed=find_params(action)
        match=echoed is not None and all(echoed.get(k)==v for k,v in args.items())
        def find_liq(obj):
            if isinstance(obj,dict):
                for key,v in obj.items():
                    if key in ('liquidation_price','liquidationPrice') and isinstance(v,(str,int,float)):return number(v)
                    found=find_liq(v)
                    if found is not None:return found
            elif isinstance(obj,list):
                for child in obj:
                    found=find_liq(child)
                    if found is not None:return found
            return None
        liq=find_liq(risk)
        direction=1 if p['side']=='long' else -1
        protected=liq is not None and liq>0 and direction*(number(p['stop'])-liq)>0
        draft['provider']={'action':action,'risk':risk,'warnings':value.get('warnings',[]),'expires_at':value.get('expiresAt')}
        draft['proposal']['liquidation']=decimal(liq) if liq is not None else None
        draft['preview_expires']=min(time.time()+75,draft['expires'],expiry(value.get('expiresAt'))-5)
        if draft['preview_expires']<=time.time():raise ValueError('Предпросмотр Walt уже истёк. Исполнения не было.')
        draft['preview_hash']=hashlib.sha256(json.dumps([args,action,risk],sort_keys=True).encode()).hexdigest()
        draft['block_reason']=None if match and protected else 'Не подтверждены точное действие/уровни либо стоп до ликвидации. Исполнение заблокировано.'
        self.agent.put_secret('trade:'+ident,{'token':value['confirmationToken']})
        with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET status=?,payload=? WHERE id=?',('previewed' if not draft['block_reason'] else 'blocked',json.dumps(draft,ensure_ascii=False),ident))
        return self.public(ident)

    def execute(self,ident,preview_hash,confirm):
        if confirm!='OPEN_REAL_POSITION':raise ValueError('Нужно явное подтверждение реальной сделки.')
        owner=secrets.token_hex(16)
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM walt_trade_drafts WHERE id=?',(ident,)).fetchone()
            if not row:raise ValueError('Сделка не найдена.')
            draft=json.loads(row['payload'])
            if row['status'] in ('executing','uncertain','submitted','protected'):return self.public(ident)
            if row['status']!='previewed' or draft.get('preview_hash')!=preview_hash or time.time()>draft.get('preview_expires',0):raise ValueError('Предпросмотр устарел или изменён. Подготовь сделку заново.')
            unresolved=db.execute("SELECT id FROM walt_trade_drafts WHERE status IN ('executing','uncertain','submitted') LIMIT 1").fetchone()
            if unresolved:raise ValueError('Есть неподтверждённая предыдущая отправка. Сначала проверь её состояние в Walt.')
            lock=db.execute('SELECT * FROM walt_trade_lock WHERE id=1').fetchone()
            if lock and lock['expires']>time.time():raise ValueError('Другая операция уже проверяется.')
            db.execute('INSERT OR REPLACE INTO walt_trade_lock VALUES(1,?,?)',(owner,time.time()+600))
        attempted=False
        try:
            sid=self.agent.connect();self.ensure_current(draft,sid)
            if time.time()>draft['preview_expires']:raise ValueError('Предпросмотр истёк во время проверки.')
            secret=self.agent.secret('trade:'+ident)
            if not secret:raise ValueError('Одноразовое подтверждение Walt недоступно.')
            with self.agent.db() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute("UPDATE walt_trade_drafts SET status='executing' WHERE id=? AND status='previewed'",(ident,)).rowcount
                if not changed:raise ValueError('Сделка уже обрабатывается.')
            attempted=True
            result=unpack(self.agent.rpc('tools/call',{'name':'perps_execute_operation','arguments':{'confirmationToken':secret['token']}},sid,_manual=True)[0])
            draft['execution']={'result':result,'at':now()}
            with self.agent.db() as db:db.execute("UPDATE walt_trade_drafts SET status='submitted',payload=? WHERE id=?",(json.dumps(draft,ensure_ascii=False),ident))
            return self.reconcile(ident)
        except Exception:
            if attempted:
                draft['alert']='Отправка была предпринята. Результат не подтверждён; автоматического повтора нет. Проверь позиции и заявки в Walt.'
                with self.agent.db() as db:db.execute("UPDATE walt_trade_drafts SET status='uncertain',payload=? WHERE id=?",(json.dumps(draft,ensure_ascii=False),ident))
                return self.public(ident)
            raise
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_trade_lock WHERE owner=?',(owner,))

    def reconcile(self,ident):
        row,draft=self.row(ident)
        if row['status'] not in ('submitted','uncertain','executing','protected'):return self.public(ident)
        sid=self.agent.connect();p=draft['proposal']
        positions=self.read('perps_list_positions',{'contractId':p['contract_id'],'statuses':['open']},sid).get('positions',[])
        draft['verified_at']=now();draft['positions_seen']=positions
        status=row['status']
        if positions:
            position=positions[0]
            try:
                stop=number(position['auto_close']['stop_loss']['trigger_price_quote']);take=number(position['auto_close']['take_profit']['trigger_price_quote'])
                liq=number(position['liquidation_price']);direction=1 if p['side']=='long' else -1
                matched=position.get('side')==p['side'] and abs(stop-number(p['stop']))<=number(draft['contract']['tick_size']) and abs(take-number(p['take']))<=number(draft['contract']['tick_size']) and direction*(stop-liq)>0
            except (KeyError,TypeError,ValueError):matched=False
            if matched:status='protected';draft['alert']=None
            else:
                status='uncertain';draft['alert']='СРОЧНО: позиция обнаружена, но стоп/тейк и защита от ликвидации не подтверждены. Проверь и исправь уровни в Walt; новые сделки заблокированы.'
        else:
            draft['alert']='Открытая позиция пока не обнаружена. Заявка может обрабатываться или быть отклонена. Проверь Walt; повтор не выполняется.'
        with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET status=?,payload=? WHERE id=?',(status,json.dumps(draft,ensure_ascii=False),ident))
        return self.public(ident)

    def overview(self):
        with self.agent.db() as db:
            rows=db.execute('SELECT id FROM walt_trade_drafts ORDER BY at DESC LIMIT 12').fetchall()
            latest=db.execute('SELECT payload FROM walt_trade_signals ORDER BY at DESC LIMIT 1').fetchone()
        token=self.agent.secret('tokens')
        return {'limits':self.limits(),'trade_authorized':bool(token and 'perps.trade' in token.get('scope','').split()),
                'latest_signal':json.loads(latest[0]) if latest else None,'drafts':[self.public(row['id']) for row in rows]}
