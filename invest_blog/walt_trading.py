"""Signal -> deterministic sizing -> provider preview -> explicit user execution.

The model never receives a trade token or access to execution tools.
"""
from datetime import datetime,timezone
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING,InvalidOperation
import hashlib
import json
import secrets
import time
import re

from .walt_agent import WaltAgent,now
from .walt_history import LighterHistory,assess,review_candidates,STRATEGY
from .walt_news import TradingNews
from .walt_preview import verify_action,verify_risk,protection_evidence,contract_fingerprint
from .walt_errors import WaltRPCError,error_code


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


def unpack_execution(result):
    if not isinstance(result,dict):raise WaltRPCError('format')
    if result.get('isError'):raise WaltRPCError('tool',error_code(result))
    value=result.get('structuredContent')
    if value is None:
        for item in result.get('content',[]):
            if item.get('type')=='text':
                try:value=json.loads(item['text'])
                except (ValueError,TypeError):continue
                break
    # Execute may return an order id instead of a position object. The order
    # and actual protective legs still require a separate read-only verification.
    if isinstance(value,(str,int)) and not isinstance(value,bool):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',str(value)):raise WaltRPCError('format')
        value={'order_id':str(value)}
    if not isinstance(value,dict):raise WaltRPCError('format')
    if value.get('ok') is False or value.get('error'):raise WaltRPCError('tool',error_code(value))
    return value


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
    symbol=str(symbol).upper()
    if symbol in ('NVDA','AAPL','MSFT','AMD','GOOGL','GOOG','META','AMZN','TSLA','AVGO','INTC'):return 'technology'
    if symbol in ('XAU','XAG','XCU','GOLD','SILVER','COPPER','OIL','BRENT','WTI'):return 'commodities'
    currencies={'USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD','CNH','CNY','KRW','BRL','MXN'}
    if len(symbol)==6 and symbol[:3] in currencies and symbol[3:] in currencies and symbol[:3]!=symbol[3:]:return 'forex'
    if symbol in ('BTC','ETH','SOL','XRP','BNB','DOGE','ADA','AVAX','LINK','DOT','LTC','BCH','TRX','TON','SUI','APT','NEAR','ARB','OP','UNI','AAVE','ZEC','HYPE','GRAM'):return 'crypto'
    return 'other'  # Conservative shared bucket for unclassified assets, not an inferred industry.


def check_group_limit(symbol,catalog,positions,limit):
    group=group_for(symbol)
    held=[catalog[p['contract_id']]['base_asset'] for p in positions if group_for(catalog[p['contract_id']]['base_asset'])==group]
    if len(held)>=limit:
        label={'technology':'Технологические акции','commodities':'Сырьевые активы','forex':'Валютные пары',
            'crypto':'Криптоактивы','other':'Неклассифицированные активы'}[group]
        raise ValueError(f'Лимит группы «{label}»: занято {len(held)} из {limit}. Открыты: {", ".join(held)}. '
            f'Новая позиция {symbol} не рассчитана. Лонг и шорт учитываются в одном лимите; заявки не отправлялись.')


DEFAULT_LIMITS={'revision':1,'leverage':5,'risk_pct':'1','portfolio_risk_pct':'5',
    'margin_free_pct':'25','margin_equity_pct':'35','min_margin':'2','max_margin':'10',
    'stop_pct':'3.5','take_pct':'10','max_positions':4,'max_group_positions':2,'max_drift_pct':'0.5'}


def snapshot_assessment():
    # A prompt is not an evidence check. A single observation proves neither
    # direction of price/OI nor a trade thesis, regardless of the model's answer.
    return {'side':'wait','reason':'Подтверждённого направления нет: получен один снимок цены и OI. '
            'Рост/падение, тренд и изменение OI не установлены. Фандинг сам по себе не подтверждает вход.'}


def safe_signal(packet):
    if packet is not None:
        packet=json.loads(json.dumps(packet))
        for row in packet.get('candidates',[]):
            a=row.get('assessment',{});review=row.get('model_review',{});ai=row.get('ai',{})
            trusted=packet.get('analysis_version')==3 and a.get('strategy_version')==STRATEGY and a.get('data_verified') is True
            if not trusted:
                row['ai']={'side':'wait','reason':a.get('reason','История не подтверждена.')} if packet.get('analysis_version')==3 else snapshot_assessment()
            elif ai.get('side') not in ('wait',a.get('rule_side')) or (ai.get('side')!='wait' and review.get('decision')!='review'):
                row['ai']={'side':'wait','reason':'Подтверждённая проверка кандидата отсутствует.'}
    return packet


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
    if amount<minimum:raise ValueError(
        f'Баланс прочитан: {decimal(free)}. При плече {leverage}× и риске {limits["risk_pct"]}% '
        f'допустимая маржа — {decimal(max(Decimal(0),amount))}, минимум — {decimal(minimum)}. '
        'Сделка пропущена. Можно уменьшить плечо в «Лимитах сделки» и пересчитать; риск автоматически не увеличивается.')
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
    def __init__(self,agent,history_factory=LighterHistory,news_factory=TradingNews):
        self.agent=agent;self.history_factory=history_factory;self.news_factory=news_factory
        with agent.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS walt_trade_limits(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_trade_signals(id TEXT PRIMARY KEY,at REAL,payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_trade_scan(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
              CREATE TABLE IF NOT EXISTS walt_wallet_snapshot(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT);
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
        # Walt settles the agent account in USDT, while its venue catalogue uses
        # USDC quote units. These are provider nominal amounts, not a spot FX trade.
        accounts=[b for b in balances if str(b.get('currency','')).upper() in ('USDT','USDC')]
        if len(accounts)!=1:raise ValueError('Walt не подтвердил единственный баланс обеспечения USDT/USDC. Несколько валют не суммируются; проверь агентский счёт.')
        currency=str(accounts[0]['currency']).upper()
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
        return {'free':free,'equity':equity,'risk':risk,'positions':positions,'currency':currency}

    def signal(self):
        owner=secrets.token_hex(16)
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM walt_agent_lock WHERE id=1').fetchone()
            if row and row['expires']>time.time():raise ValueError('Проверка рынка или анализ уже выполняется. Дождись завершения.')
            db.execute('INSERT OR REPLACE INTO walt_agent_lock VALUES(1,?,?)',(owner,time.time()+900))
        self.scan_progress({'status':'running','done':0,'total':0,'errors':0,'symbol':None,'started':time.time()})
        try:return self._signal()
        except Exception:
            progress=self.scan_progress();progress.update(status='failed',symbol=None)
            self.scan_progress(progress)
            raise
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_agent_lock WHERE owner=?',(owner,))

    def scan_progress(self,value=None):
        with self.agent.db() as db:
            if value is not None:
                value={**value,'updated':time.time()}
                db.execute('INSERT OR REPLACE INTO walt_trade_scan VALUES(1,?)',(json.dumps(value),))
                if value.get('status')=='running':db.execute('UPDATE walt_agent_lock SET expires=? WHERE id=1',(time.time()+900,))
            row=db.execute('SELECT payload FROM walt_trade_scan WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {'status':'idle','done':0,'total':0,'errors':0}

    def _signal(self):
        sid=self.agent.connect();catalog=self.catalog(sid)
        selected=sorted(catalog.values(),key=lambda c:(-number(c.get('volume_24h_usd') or 0),c['contract_id']))
        rows=[];errors=[];analysis_limits=self.limits()
        started=time.time()
        histories={};history_errors={};history=self.history_factory(self.agent)
        for index,c in enumerate(selected):
            self.scan_progress({'status':'running','phase':'history','done':index,'total':len(selected),'errors':len(history_errors),'symbol':c['base_asset'],'started':started})
            try:histories[c['contract_id']]=history.load(c)
            except ValueError as exc:history_errors[c['contract_id']]=str(exc)
            with self.agent.db() as db:db.execute('UPDATE walt_agent_lock SET expires=? WHERE id=1',(time.time()+900,))
        news=self.news_factory(self.agent)
        news_snapshot=news.collect(lambda done,total,name:self.scan_progress({'status':'running','phase':'news','done':done,
            'total':total,'errors':0,'symbol':name,'started':started}))
        for c in selected:
            self.scan_progress({'status':'running','phase':'quotes','done':len(rows),'total':len(selected),'errors':len(errors),'symbol':c['base_asset'],'started':started})
            row={'contract_id':c['contract_id'],'symbol':c['base_asset'],'currency':c['quote_asset'],
                 'price':None,'price_available':False,'volume_24h_usd':c.get('volume_24h_usd'),
                 'open_interest_usd':c.get('open_interest_usd'),'funding_rate_hourly':c.get('funding_rate_hourly')}
            try:
                price=self.price(c,sid)
                row.update(price=decimal(price),price_available=True)
            except ValueError:
                errors.append(c['base_asset']);row['price_error']='Walt не подтвердил котировку. Расчёт недоступен.'
            row.update(observed_at=now(),observed=time.time())
            row['news']=news.context(news_snapshot,c['base_asset'])
            h=histories.get(c['contract_id'])
            row['ai']=snapshot_assessment()
            if h and row['price_available']:
                try:
                    try:oi=history.observation(row)
                    except ValueError:oi=None
                    row['assessment']=assess(h,row,c,analysis_limits,oi)
                    row['ai']={'side':'wait','reason':row['assessment']['reason']}
                except ValueError as exc:history_errors[c['contract_id']]=str(exc)
            if 'assessment' not in row:
                row['assessment']={'data_verified':False,'rule_side':'wait','reason':history_errors.get(c['contract_id'],'Котировка Walt недоступна.')}
                row['ai']={'side':'wait','reason':row['assessment']['reason']}
            rows.append(row)
            # Renew the lease during a full catalogue scan; no calls to write tools.
            with self.agent.db() as db:db.execute('UPDATE walt_agent_lock SET expires=? WHERE id=1',(time.time()+900,))
        self.scan_progress({'status':'running','phase':'model','done':len(rows),'total':len(selected),'errors':len(errors),'symbol':None,'started':started})
        reviewed=review_candidates(self.agent,rows,lambda done,total:self.scan_progress({'status':'running','phase':'model','done':done,'total':total,'errors':0,'symbol':None,'started':started}))
        covered=sum(r.get('assessment',{}).get('data_verified') is True for r in rows)
        proposed=sum(r['ai']['side'] in ('long','short') for r in rows)
        packet={'id':secrets.token_hex(16),'created':time.time(),'expires':time.time()+300,'candidates':rows,'errors':errors,
                'total':len(catalog),'quotes_available':len(rows)-len(errors),'started':started,'full_catalog':True,
                'analysis_version':3,'history_available':covered,'history_errors':history_errors,'model_reviewed':reviewed,'ideas':proposed,
                'analysis_parameters':{k:analysis_limits[k] for k in ('stop_pct','take_pct')},
                'news_summary':{**{k:v for k,v in news_snapshot.items() if k!='items'},'recent_items':len(news_snapshot['items']),
                    'matched_assets':sum(r['news']['status']=='matched' for r in rows)},
                'coverage':f'Проверено {len(rows)} из {len(catalog)} контрактов Walt. Котировки: {len(rows)-len(errors)}; история: {covered}; кандидаты после проверки ИИ: {proposed}.',
                'limitations':'История: закрытые часовые свечи Lighter, около 20 дней. Новости: ограниченные RSS за 48ч, не полный поток и не календарь событий. Стратегия экспериментальная. Исторический фандинг не проверен; изменение OI — только по сохранённым наблюдениям.'}
        with self.agent.db() as db:db.execute('INSERT INTO walt_trade_signals VALUES(?,?,?)',(packet['id'],packet['created'],json.dumps(packet,ensure_ascii=False)))
        self.scan_progress({'status':'complete','done':len(rows),'total':len(catalog),'errors':len(errors),'symbol':None,'started':started})
        return packet

    def row(self,ident):
        with self.agent.db() as db:row=db.execute('SELECT * FROM walt_trade_drafts WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Сделка не найдена.')
        return dict(row),json.loads(row['payload'])

    def quote(self,signal_id,contract_id,side,requested=None):
        with self.agent.db() as db:row=db.execute('SELECT payload FROM walt_trade_signals WHERE id=?',(signal_id,)).fetchone()
        if not row:raise ValueError('Сначала нажми «Сигнал».')
        packet=safe_signal(json.loads(row[0]))
        if packet.get('analysis_parameters') and any(number(v)!=number(self.limits()[k]) for k,v in packet['analysis_parameters'].items()):
            raise ValueError('Стоп или тейк изменились после ретроспективы. Обнови анализ всех инструментов перед расчётом.')
        if time.time()>packet['expires'] or contract_id not in [r['contract_id'] for r in packet['candidates']]:raise ValueError('Сигнал устарел или инструмент не входит в выборку.')
        candidate=next(r for r in packet['candidates'] if r['contract_id']==contract_id)
        if candidate.get('price_available') is False:raise ValueError('Для этого инструмента Walt не подтвердил котировку. Повтори сбор данных позже.')
        if candidate.get('observed') and time.time()-candidate['observed']>300:raise ValueError('Котировка инструмента старше пяти минут. Обнови данные.')
        sid=self.agent.connect();catalog=self.catalog(sid);c=catalog.get(contract_id)
        if not c:raise ValueError('Инструмент больше недоступен в Walt.')
        account=self.account(catalog,sid);limits=self.limits()
        if len(account['positions'])>=limits['max_positions']:raise ValueError('Достигнут лимит открытых позиций.')
        if any(p.get('contract_id')==contract_id for p in account['positions']):raise ValueError('По этому контракту уже есть позиция. Наращивание не поддерживается.')
        check_group_limit(c['base_asset'],catalog,account['positions'],limits['max_group_positions'])
        proposal=calculate(c,self.price(c,sid),account['free'],account['equity'],limits,side,account['risk'],requested)
        proposal['quote_currency']=c['quote_asset']
        proposal['currency']=account['currency']
        ident=secrets.token_hex(16)
        draft={'id':ident,'proposal':proposal,'contract':c,'created':time.time(),'expires':time.time()+300,
               'limits_revision':limits['revision'],'generation':self.agent.state()['generation'],'evidence_version':3,
               'assessment':candidate.get('assessment'),
               'news':candidate.get('news'),'model_review':candidate.get('model_review'),
               'ai':next(r['ai'] for r in packet['candidates'] if r['contract_id']==contract_id),'warnings':[
                   'Реальные средства '+account['currency']+'. Котировки и номиналы контракта — '+c['quote_asset']+' по API Walt; обмен хабом не выполняется. Рыночная цена исполнения не гарантируется.',
                   'Риск включает оценку комиссий и проскальзывания, но гэп и изменение фандинга могут увеличить убыток.',
                   'Цель 10% от цены и доходность 10% на маржу — разные показатели.']}
        if draft['ai']['side']!=side:
            draft['warnings'].append('Выбранное направление не подтверждено текущим анализом. Это твой ручной сценарий, а не сигнал стратегии.')
        with self.agent.db() as db:db.execute('INSERT INTO walt_trade_drafts VALUES(?,?,?,?)',(ident,time.time(),'calculated',json.dumps(draft,ensure_ascii=False)))
        return self.public(ident)

    def public(self,ident):
        row,draft=self.row(ident)
        if draft.get('evidence_version')!=3:draft['ai']=snapshot_assessment()
        def redact(value):
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items() if not any(word in k.lower() for word in ('token','secret','verifier'))}
            if isinstance(value,list):return [redact(v) for v in value]
            return value
        return redact({k:v for k,v in {**draft,'status':row['status']}.items() if k not in ('contract',)})

    def ensure_current(self,draft,sid):
        if draft.get('evidence_version')!=3:raise ValueError('Правила анализа обновились. Проверь рынок и пересчитай сделку заново.')
        if draft['generation']!=self.agent.state()['generation']:raise ValueError('Подключение Walt изменилось. Рассчитай сделку заново.')
        if draft['limits_revision']!=self.limits()['revision']:raise ValueError('Лимиты изменились. Рассчитай сделку заново.')
        catalog=self.catalog(sid);c=catalog.get(draft['proposal']['contract_id'])
        if not c:raise ValueError('Контракт недоступен.')
        account=self.account(catalog,sid);p=draft['proposal'];price=self.price(c,sid);limits=self.limits()
        if p.get('group')!=group_for(c['base_asset']):raise ValueError('Классификация инструмента обновилась. Нужны новый расчёт и подтверждение.')
        if p.get('currency')!=account['currency']:raise ValueError('Валюта обеспечения изменилась. Нужен новый расчёт.')
        if min(int(limits['leverage']),int(c['max_leverage']))!=p['leverage']:raise ValueError('Допустимое плечо изменилось. Нужен новый расчёт.')
        if abs(price-number(p['reference_price']))/number(p['reference_price'])*100>number(limits['max_drift_pct']):raise ValueError('Цена изменилась сильнее допустимого. Нужен новый расчёт и подтверждение.')
        if len(account['positions'])>=limits['max_positions'] or any(pos.get('contract_id')==c['contract_id'] for pos in account['positions']):raise ValueError('Состав позиций изменился. Проверь кошелёк и пересчитай сделку.')
        check_group_limit(c['base_asset'],catalog,account['positions'],limits['max_group_positions'])
        calculate(c,price,account['free'],account['equity'],limits,p['side'],account['risk'],p['margin'],fixed=p)
        return c

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
        sid=self.agent.connect();current_contract=self.ensure_current(draft,sid);p=draft['proposal']
        args={'contractId':p['contract_id'],'side':p['side'],'type':'market','leverage':p['leverage'],
              'amount':p['margin'],'price':p['reference_price'],'autoClose':{'takeProfit':{'priceQuote':p['take']},'stopLoss':{'priceQuote':p['stop']}}}
        value=unpack(self.agent.rpc('tools/call',{'name':'perps_prepare_order_place','arguments':args},sid,_manual=True)[0])
        if value.get('ok') is not True or not value.get('confirmationToken'):
            checks=value.get('risk',{}).get('checks',[])
            failures=[str(c.get('code') or c.get('name') or c.get('id') or '') for c in checks if isinstance(c,dict) and (c.get('ok') is False or c.get('passed') is False)]
            raise ValueError('Walt отклонил предварительную проверку. Исполнения не было. '+', '.join(failures))
        action=value.get('action');risk=value.get('risk')
        failures=[];evidence=None
        for check in (lambda:verify_action(action,args),lambda:verify_risk(risk)):
            try:check()
            except ValueError as exc:failures.append(str(exc))
        try:
            evidence=protection_evidence(p,current_contract,risk if isinstance(risk,dict) else {})
            if not evidence['safe']:failures.append('Стоп с учётом проскальзывания не имеет достаточного запаса до границы ликвидации.')
        except ValueError as exc:failures.append(str(exc))
        draft['provider']={'action':action,'risk':risk,'warnings':value.get('warnings',[]),'expires_at':value.get('expiresAt')}
        draft['proposal']['liquidation']=evidence['price'] if evidence else None
        draft['proposal']['liquidation_source']=evidence['source'] if evidence else None
        draft['liquidation_evidence']=evidence
        draft['preview_validation_version']=2
        draft['contract']=current_contract
        draft['preview_expires']=min(time.time()+75,draft['expires'],expiry(value.get('expiresAt'))-5)
        if draft['preview_expires']<=time.time():raise ValueError('Предпросмотр Walt уже истёк. Исполнения не было.')
        draft['preview_hash']=hashlib.sha256(json.dumps([args,action,risk,evidence],sort_keys=True).encode()).hexdigest()
        draft['block_reason']=' '.join(failures)+' Исполнение заблокировано.' if failures else None
        if not failures:self.agent.put_secret('trade:'+ident,{'token':value['confirmationToken']})
        else:
            with self.agent.db() as db:db.execute('DELETE FROM walt_agent_secrets WHERE name=?',('trade:'+ident,))
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
            if row['status'] in ('executing','uncertain','submitted','protected','not_open'):return self.public(ident)
            if row['status']!='previewed' or draft.get('preview_hash')!=preview_hash or time.time()>draft.get('preview_expires',0):raise ValueError('Предпросмотр устарел или изменён. Подготовь сделку заново.')
            unresolved=db.execute("SELECT id FROM walt_trade_drafts WHERE status IN ('executing','uncertain','submitted') LIMIT 1").fetchone()
            if unresolved:raise ValueError('Есть неподтверждённая предыдущая отправка. Сначала проверь её состояние в Walt.')
            lock=db.execute('SELECT * FROM walt_trade_lock WHERE id=1').fetchone()
            if lock and lock['expires']>time.time():raise ValueError('Другая операция уже проверяется.')
            db.execute('INSERT OR REPLACE INTO walt_trade_lock VALUES(1,?,?)',(owner,time.time()+600))
        attempted=False
        try:
            if draft.get('preview_validation_version')!=2:raise ValueError('Проверка предпросмотра обновилась. Получи новый предпросмотр и подтверди его заново.')
            sid=self.agent.connect();current_contract=self.ensure_current(draft,sid)
            proof=draft.get('liquidation_evidence') or {}
            if proof.get('contract_fingerprint')!=contract_fingerprint(current_contract):
                raise ValueError('Параметры защиты контракта изменились. Получи новый предпросмотр.')
            if not protection_evidence(draft['proposal'],current_contract,draft['provider']['risk'])['safe']:
                raise ValueError('Запас защиты изменился. Нужны новый расчёт и подтверждение.')
            if time.time()>draft['preview_expires']:raise ValueError('Предпросмотр истёк во время проверки.')
            secret=self.agent.secret('trade:'+ident)
            if not secret:raise ValueError('Одноразовое подтверждение Walt недоступно.')
            with self.agent.db() as db:
                db.execute('BEGIN IMMEDIATE')
                changed=db.execute("UPDATE walt_trade_drafts SET status='executing' WHERE id=? AND status='previewed'",(ident,)).rowcount
                if not changed:raise ValueError('Сделка уже обрабатывается.')
            draft['attempted_at']=now()
            with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(draft,ensure_ascii=False),ident))
            attempted=True;phase='send'
            raw_result=self.agent.rpc('tools/call',{'name':'perps_execute_operation','arguments':{'confirmationToken':secret['token']}},sid,_manual=True)[0]
            phase='parse';result=unpack_execution(raw_result)
            draft['execution']={'result':result,'at':now()}
            with self.agent.db() as db:db.execute("UPDATE walt_trade_drafts SET status='submitted',payload=? WHERE id=?",(json.dumps(draft,ensure_ascii=False),ident))
            phase='verify'
            return self._reconcile(ident)
        except Exception as exc:
            if attempted:
                diagnostic=getattr(exc,'diagnostic',{'kind':'local','code':None,'http_status':None})
                draft['execution_issue']={**diagnostic,'phase':phase,'at':now()}
                labels={'send':'Сбой при отправке','parse':'Ответ получен, но не разобран','verify':'Ответ об отправке получен, но проверка позиции не завершена'}
                detail=str(exc) if isinstance(exc,WaltRPCError) else 'Внутренняя проверка не завершилась.'
                draft['alert']=labels[phase]+'. '+detail+' Результат сделки не подтверждён. Проверь позиции и заявки Walt; повтор не выполняется.'
                with self.agent.db() as db:db.execute("UPDATE walt_trade_drafts SET status='uncertain',payload=? WHERE id=?",(json.dumps(draft,ensure_ascii=False),ident))
                return self.public(ident)
            raise
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_trade_lock WHERE owner=?',(owner,))

    def reconcile(self,ident):
        with self.wallet_read_lock():return self._reconcile(ident)

    @contextmanager
    def wallet_read_lock(self):
        owner=secrets.token_hex(16)
        with self.agent.db() as db:
            db.execute('BEGIN IMMEDIATE')
            lock=db.execute('SELECT * FROM walt_trade_lock WHERE id=1').fetchone()
            if lock and lock['expires']>time.time():raise ValueError('Операция Walt уже выполняется. Дождись завершения и повтори обновление.')
            db.execute('INSERT OR REPLACE INTO walt_trade_lock VALUES(1,?,?)',(owner,time.time()+600))
        try:yield
        finally:
            with self.agent.db() as db:db.execute('DELETE FROM walt_trade_lock WHERE owner=?',(owner,))

    def _reconcile(self,ident):
        row,draft=self.row(ident)
        if row['status'] not in ('submitted','uncertain','executing','protected','not_open'):return self.public(ident)
        sid=self.agent.connect();p=draft['proposal']
        positions=self.read('perps_list_positions',{'contractId':p['contract_id'],'statuses':['open']},sid).get('positions',[])
        if not isinstance(positions,list):raise ValueError('Walt не подтвердил список позиций. Предыдущее состояние сохранено.')
        result=self.reconcile_positions(ident,positions)
        return self.check_orders(ident,sid)

    def check_orders(self,ident,sid):
        row,draft=self.row(ident);p=draft['proposal']
        # Exact order id is authoritative. A contract history alone is not proof
        # that a previous order belongs to this draft.
        result=draft.get('execution',{}).get('result',{})
        order=result.get('order') if isinstance(result.get('order'),dict) else result
        order_id=order.get('order_id',order.get('orderId',order.get('id')))
        if order_id is not None:
            value=self.read('perps_get_order',{'orderId':str(order_id)},sid)
            orders=[value.get('order',value)];complete=True
        else:
            value=self.read('perps_list_orders',{'contractId':p['contract_id'],'limit':100},sid)
            orders=value.get('orders');complete=not value.get('next_cursor')
        if not isinstance(orders,list) or any(not isinstance(o,dict) for o in orders):raise ValueError('Walt не подтвердил список заявок.')
        safe=[{k:o.get(k) for k in ('id','order_id','contract_id','side','type','status','created_at','updated_at')} for o in orders]
        draft['orders_checked']={'at':now(),'orders':safe,'complete':complete,'exact_order':order_id is not None}
        if row['status'] in ('uncertain','submitted','executing') and not draft.get('positions_seen'):
            if order_id is not None and len(orders)==1 and orders[0].get('status') in ('pending','processing','partially_filled'):
                draft['alert']='Заявка принята Walt и ещё обрабатывается. Открытие позиции пока не подтверждено. Повторная отправка не нужна.'
            elif order_id is not None and len(orders)==1 and orders[0].get('status')=='filled':
                draft['alert']='Walt подтвердил исполнение заявки. Ожидается сверка открытой позиции и защитных уровней. Повторная отправка не нужна.'
            elif not orders and complete:draft['alert']='Walt: открытой позиции и заявок по этому контракту не найдено. Причина прошлой отправки не подтверждена. Автоматического повтора нет.'
            else:draft['alert']='Walt вернул заявки по контракту. Проверь их статусы ниже; принадлежность расчёту подтверждается только точным ID. Повтор не выполняется.'
        with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET payload=? WHERE id=?',(json.dumps(draft,ensure_ascii=False),ident))
        return self.public(ident)

    def resolve_absent(self,ident,confirm):
        if confirm!='CONFIRM_NO_POSITION_OR_ORDER':raise ValueError('Сначала самостоятельно проверь позиции и ожидающие заявки в Walt.')
        with self.wallet_read_lock():
            row,draft=self.row(ident)
            if row['status']!='uncertain':raise ValueError('Этот расчёт не требует ручного завершения проверки.')
            if time.time()-draft['created']<120:raise ValueError('Подожди минимум две минуты и проверь Walt повторно.')
            self._reconcile(ident);row,draft=self.row(ident)
            check=draft.get('orders_checked',{})
            if row['status']!='uncertain' or draft.get('positions_seen') or not check.get('complete') or check.get('orders'):
                raise ValueError('Есть позиция, заявки или неполный ответ Walt. Завершить проверку нельзя.')
            draft['resolution']={'at':now(),'reason':'user_confirmed_absent_after_readonly_check'}
            draft['alert']='Ты подтвердил отсутствие позиции и заявки в Walt; повторная проверка также пуста. Причина прошлой отправки неизвестна. Старое подтверждение аннулировано. Новая сделка требует нового расчёта и ручного подтверждения.'
            with self.agent.db() as db:
                db.execute('DELETE FROM walt_agent_secrets WHERE name=?',('trade:'+ident,))
                db.execute("UPDATE walt_trade_drafts SET status='resolved_absent',payload=? WHERE id=?",(json.dumps(draft,ensure_ascii=False),ident))
            return self.public(ident)

    def reconcile_positions(self,ident,positions):
        row,draft=self.row(ident);p=draft['proposal']
        positions=[position for position in positions if position.get('contract_id')==p['contract_id']]
        draft['verified_at']=now();draft['positions_seen']=positions
        status=row['status']
        if positions:
            position=positions[0]
            levels=position.get('auto_close') or {}
            draft['actual_position']={key:position.get(key) for key in ('side','leverage','margin_quote','avg_entry_price','value_base','liquidation_price','funding_paid_quote')}
            for source,dest in (('stop_loss','stop'),('take_profit','take')):
                leg=levels.get(source) if isinstance(levels,dict) else None
                draft['actual_position'][dest]=leg.get('trigger_price_quote') if isinstance(leg,dict) else None
            draft['actual_position']['checked_at']=draft['verified_at']
            try:
                stop=number(position['auto_close']['stop_loss']['trigger_price_quote']);take=number(position['auto_close']['take_profit']['trigger_price_quote'])
                liq=number(position['liquidation_price']);direction=1 if p['side']=='long' else -1
                matched=position.get('side')==p['side'] and abs(stop-number(p['stop']))<=number(draft['contract']['tick_size']) and abs(take-number(p['take']))<=number(draft['contract']['tick_size']) and direction*(stop-liq)>0
            except (KeyError,TypeError,ValueError):matched=False
            if matched:status='protected';draft['alert']=None
            else:
                status='uncertain';draft['alert']='СРОЧНО: позиция обнаружена, но стоп/тейк и защита от ликвидации не подтверждены. Проверь и исправь уровни в Walt; новые сделки заблокированы.'
        else:
            draft.pop('actual_position',None)
            if status in ('protected','not_open'):
                status='not_open';draft['alert']='Ранее подтверждённая позиция сейчас не обнаружена среди открытых. Для причины и результата закрытия проверь историю Walt.'
            else:draft['alert']='Открытая позиция пока не обнаружена. Заявка может обрабатываться или быть отклонена. Проверь Walt; повтор не выполняется.'
        with self.agent.db() as db:db.execute('UPDATE walt_trade_drafts SET status=?,payload=? WHERE id=?',(status,json.dumps(draft,ensure_ascii=False),ident))
        return self.public(ident)

    def sync_wallet(self):
        """Explicit wallet read. Never prepare, execute or retry an order."""
        with self.wallet_read_lock():return self._sync_wallet()

    def _sync_wallet(self):
        sid=self.agent.connect()
        positions=self.read('perps_list_positions',{'statuses':['open']},sid).get('positions')
        if not isinstance(positions,list) or any(not isinstance(p,dict) for p in positions):
            raise ValueError('Walt не подтвердил список открытых позиций. Предыдущий снимок сохранён.')
        catalog=self.catalog(sid)
        fields=('contract_id','side','margin_quote','avg_entry_price','value_base','liquidation_price','funding_paid_quote')
        visible=[]
        for position in positions:
            item={key:position.get(key) for key in fields}
            item['symbol']=catalog.get(position.get('contract_id'),{}).get('base_asset')
            levels=position.get('auto_close') or {}
            if not isinstance(levels,dict):levels={}
            for source,dest in (('stop_loss','stop'),('take_profit','take')):
                leg=levels.get(source)
                item[dest]=leg.get('trigger_price_quote') if isinstance(leg,dict) else None
            visible.append(item)
        with self.agent.db() as db:
            drafts=db.execute("SELECT id FROM walt_trade_drafts WHERE status IN ('submitted','uncertain','executing','protected','not_open')").fetchall()
        for draft in drafts:
            item=self.reconcile_positions(draft['id'],positions)
            if item['status'] in ('uncertain','submitted','executing','protected'):self.check_orders(draft['id'],sid)
        snapshot={'checked_at':now(),'positions':visible,'open_count':len(visible)}
        with self.agent.db() as db:db.execute('INSERT OR REPLACE INTO walt_wallet_snapshot VALUES(1,?)',(json.dumps(snapshot),))
        return snapshot

    def overview(self):
        with self.agent.db() as db:
            rows=db.execute('SELECT id FROM walt_trade_drafts ORDER BY at DESC LIMIT 12').fetchall()
            latest=db.execute('SELECT payload FROM walt_trade_signals ORDER BY at DESC LIMIT 1').fetchone()
            wallet=db.execute('SELECT payload FROM walt_wallet_snapshot WHERE id=1').fetchone()
        token=self.agent.secret('tokens')
        packet=safe_signal(json.loads(latest[0])) if latest else None
        candidates=packet.get('candidates',[]) if packet else []
        diagnostics={'technical':sum(r.get('assessment',{}).get('rule_side') in ('long','short') for r in candidates),
            'approved':sum(r.get('ai',{}).get('side') in ('long','short') for r in candidates),
            'blocked_by':dict(Counter(check['key'] for r in candidates for check in r.get('assessment',{}).get('checks',[]) if not check['ok']))}
        return {'limits':self.limits(),'trade_authorized':bool(token and 'perps.trade' in token.get('scope','').split()),
                'latest_signal':packet,'diagnostics':diagnostics,'wallet_snapshot':json.loads(wallet[0]) if wallet else None,
                'drafts':[self.public(row['id']) for row in rows]}
