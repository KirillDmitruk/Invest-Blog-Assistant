"""Persistent PAPER futures account. This module has no broker execution path."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import json
import sqlite3
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from .trading_market import indicators
from .trading_ai import configuration as ai_configuration, propose, Proposal


TZ = ZoneInfo('Europe/Moscow')
D = lambda value: Decimal(str(value))


def utcnow():
    return datetime.now(timezone.utc)


def cash(value):
    return format(D(value).quantize(D('.01')), 'f')


def initial_state():
    return {'mode': 'paper', 'enabled': False, 'strategy': 'ai', 'config_revision': 1,
            'budget': '10000', 'cash': '10000', 'high_water': '10000', 'realized_pnl': '0',
            'day': '', 'day_start_equity': '10000', 'watchlist': {}, 'positions': {}, 'marks': {},
            'risk': {'risk_pct': '1', 'daily_loss_pct': '3', 'max_drawdown_pct': '10',
                     'max_margin_pct': '30', 'max_positions': 2, 'max_contracts': 1,
                     'commission': '2', 'slippage_ticks': 1, 'margin_buffer_pct': '25'},
            'auto_pause_reason': '', 'last_cycle': None, 'worker_heartbeat': None}


def account_values(state):
    pnl = sum((D(p['accrued_pnl']) for p in state['positions'].values()), D(0))
    margin = sum((D(p['reserved_margin']) for p in state['positions'].values()), D(0))
    equity = D(state['cash'])+pnl
    return {'equity': cash(equity), 'cash': cash(state['cash']), 'unrealized_pnl': cash(pnl),
            'margin': cash(margin), 'free_margin': cash(equity-margin),
            'net_pnl': cash(equity-D(state['budget'])),
            'drawdown_pct': str(max(D(0), (D(state['high_water'])-equity)/D(state['high_water'])*100))}


class LabConflict(ValueError):
    pass


class TradingLab:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_state(id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    uid TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    uid TEXT NOT NULL, candle_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(uid,candle_at));
                CREATE TABLE IF NOT EXISTS lab_equity(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    equity TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    status TEXT NOT NULL, note TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lab_lock(id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, expires_at TEXT);
                CREATE TABLE IF NOT EXISTS lab_ai_calls(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
                    at TEXT NOT NULL, provider TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO lab_state(id,payload) VALUES(1,?)', (json.dumps(initial_state()),))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _load(self, db):
        return json.loads(db.execute('SELECT payload FROM lab_state WHERE id=1').fetchone()[0])

    def _save(self, db, state):
        db.execute('UPDATE lab_state SET payload=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))

    def overview(self):
        from .trading_statistics import daily_results
        with self.db() as db:
            state = self._load(db)
            def rows(table, limit=50):
                return [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY id DESC LIMIT ?', (limit,))]
            trades = rows('lab_trades')
            decisions = rows('lab_decisions')
            history = list(reversed(rows('lab_equity', 500)))
            for collection in (trades, decisions, history):
                for row in collection:
                    row['detail'] = json.loads(row.pop('payload'))
            return {'state': state, 'account': account_values(state), 'ai': ai_configuration(), 'daily_results':daily_results(db),
                    'trades': trades, 'decisions': decisions, 'history': history, 'runs': rows('lab_runs', 15),
                    'live_orders_available': False,
                    'notes': ['Симулятор не отправляет заявки брокеру. Реальные деньги и ИИС не используются.',
                              'ГО берётся из биржевых параметров с заданным запасом, не является персональным ГО брокера.',
                              'Комиссия и проскальзывание — настройки модели. PnL упрощённый, без полного учёта клиринга, налогов и экспирации.']}

    def configure(self, updates, revision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            if revision != state['config_revision']:
                raise LabConflict('Настройки изменились. Обнови страницу.')
            budget = str(updates.pop('budget'))
            if D(budget) != D(state['budget']):
                if state['positions'] or db.execute('SELECT 1 FROM lab_trades LIMIT 1').fetchone():
                    raise ValueError('Бюджет нельзя менять после первой виртуальной сделки: это исказит историю.')
                state.update(budget=budget, cash=budget, high_water=budget, day_start_equity=budget)
            risk = updates.pop('risk')
            state.update(updates)
            state['risk'] = risk
            if state['enabled'] and not state['watchlist'] and state['strategy'] != 'connector':
                raise ValueError('Сначала добавь хотя бы один точный фьючерсный контракт.')
            if state['enabled'] and state['strategy'] == 'ai' and not ai_configuration()['configured']:
                raise ValueError('Сначала настрой подключение ИИ в .env либо выбери явно обозначенную стратегию правил.')
            state['config_revision'] += 1
            state['auto_pause_reason'] = ''
            self._save(db, state)
            return state

    def pause(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            state['enabled'] = False
            state['config_revision'] += 1
            self._save(db, state)

    def watch(self, contract):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            if len(state['watchlist']) >= 5 and contract['uid'] not in state['watchlist']:
                raise ValueError('Для первой версии допускается не более пяти контрактов.')
            state['watchlist'][contract['uid']] = contract
            state['config_revision'] += 1
            self._save(db, state)

    def unwatch(self, uid):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            if uid in state['positions']:
                raise ValueError('Сначала закрой виртуальную позицию в этом контракте.')
            state['watchlist'].pop(uid, None)
            state['config_revision'] += 1
            self._save(db, state)

    def _trade(self, db, state, uid, action, detail, clock):
        db.execute('INSERT INTO lab_trades(at,uid,ticker,action,payload) VALUES(?,?,?,?,?)',
                   (clock.isoformat(), uid, detail['ticker'], action, json.dumps(detail, ensure_ascii=False)))

    def _fill(self, snapshot, side, risk):
        tick = D(snapshot['contract']['tick'])
        value = D(snapshot['price'])/tick
        rounded = value.to_integral_value(rounding=ROUND_CEILING if side > 0 else ROUND_FLOOR)
        return (rounded+side*int(risk['slippage_ticks']))*tick

    def _close(self, db, state, uid, snapshot, reason, clock):
        p = state['positions'][uid]
        direction = 1 if p['side'] == 'long' else -1
        fill = self._fill(snapshot, -direction, state['risk'])
        c = snapshot['contract']
        pnl = D(p['accrued_pnl']) + (fill-D(p['last_price']))/D(c['tick'])*D(c['tick_value'])*direction*p['contracts']
        fee = D(state['risk']['commission'])*p['contracts']
        state['cash'] = str(D(state['cash'])+pnl-fee)
        state['realized_pnl'] = str(D(state['realized_pnl'])+pnl-fee)
        self._trade(db, state, uid, 'close', {'ticker': c['ticker'], 'side': p['side'], 'contracts': p['contracts'],
                    'price': str(fill), 'pnl_before_exit_fee': cash(pnl), 'fee': cash(fee), 'reason': reason,
                    'entry_margin':p.get('entry_margin'),'entry_fee':p.get('entry_fee'),
                    'quoted_at': snapshot['quoted_at']}, clock)
        del state['positions'][uid]
        from .trading_rules_v2 import VERSION
        if state.get('strategy_version')==VERSION:
            from .trading_quality import record_close
            record_close(state,uid,clock.timestamp(),reason.startswith('Стоп'))

    def _mark(self, db, state, snapshots, clock):
        closed = set()
        day = clock.astimezone(TZ).date().isoformat()
        if state['day'] != day:
            state['day'] = day
            state['day_start_equity'] = account_values(state)['equity']
        for uid, p in list(state['positions'].items()):
            s = snapshots.get(uid)
            if not s:
                continue
            c = s['contract']
            direction = 1 if p['side'] == 'long' else -1
            # Optional cloud minute replay. Only bars fully after the entry minute;
            # no fills before the position existed, no ideal stop fills through gaps.
            replayed=False
            if 'protection_candles' in s:
                opened=datetime.fromisoformat(p['opened_at'].replace('Z','+00:00'))
                first=opened.replace(second=0,microsecond=0)+timedelta(minutes=1)
                checked=datetime.fromisoformat(p.get('protection_until',first.isoformat()).replace('Z','+00:00'))
                for b in sorted(s['protection_candles'],key=lambda b:b['time']):
                    stamp=datetime.fromisoformat(b['time'].replace('Z','+00:00'))
                    if stamp<max(first,checked) or stamp+timedelta(minutes=1)>clock:continue
                    o,h,l,close=(D(b[k]) for k in ('open','high','low','close'))
                    if not 0<l<=min(o,close)<=max(o,close)<=h:raise ValueError('Некорректная минутная свеча защиты.')
                    hit_stop=l<=D(p['stop']) if direction>0 else h>=D(p['stop'])
                    hit_take=h>=D(p['target']) if direction>0 else l<=D(p['target'])
                    if hit_stop or hit_take:
                        fill=(min(o,D(p['stop'])) if direction>0 else max(o,D(p['stop']))) if hit_stop else D(p['target'])
                        self._close(db,state,uid,{**s,'price':str(fill),'quoted_at':b['time']},'Стоп по минутной свече' if hit_stop else 'Цель по минутной свече',stamp)
                        closed.add(uid);replayed=True;break
                if replayed:continue
                p['protection_until']=clock.replace(second=0,microsecond=0).isoformat()
            expiry=c.get('expires_at')
            if s['tradeable'] and expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))<=clock+timedelta(days=1):
                self._close(db,state,uid,s,'Выход перед окончанием торгов контрактом',clock);closed.add(uid);continue
            delta = (D(s['price'])-D(p['last_price']))/D(c['tick'])*D(c['tick_value'])*direction*p['contracts']
            p['accrued_pnl'] = str(D(p['accrued_pnl'])+delta)
            p['last_price'] = s['price']
            p['quoted_at'] = s['quoted_at']
            p['tick'] = c['tick']
            p['tick_value'] = c['tick_value']
            p['reserved_margin'] = str(D(c['margin_'+p['side']])*p['contracts']*(1+D(state['risk']['margin_buffer_pct'])/100))
            # A stop is simulated only on a fresh observed quote, never filled at a fictional ideal price.
            price = D(s['price'])
            stop = price <= D(p['stop']) if direction > 0 else price >= D(p['stop'])
            target = price >= D(p['target']) if direction > 0 else price <= D(p['target'])
            if s['tradeable'] and (stop or target):
                self._close(db, state, uid, s, 'Стоп' if stop else 'Цель', clock)
                closed.add(uid)
        values = account_values(state)
        equity = D(values['equity'])
        state['high_water'] = str(max(D(state['high_water']), equity))
        day_loss = (D(state['day_start_equity'])-equity)/max(D(1), D(state['day_start_equity']))*100
        drawdown = (D(state['high_water'])-equity)/max(D(1), D(state['high_water']))*100
        breached = day_loss >= D(state['risk']['daily_loss_pct']) or drawdown >= D(state['risk']['max_drawdown_pct']) or equity <= D(values['margin']) and bool(state['positions'])
        if breached:
            state['enabled'] = False
            state['config_revision'] += 1
            state['auto_pause_reason'] = 'Достигнут лимит потерь или недостаточно обеспечения. Новые позиции остановлены.'
            for uid in list(state['positions']):
                if uid in snapshots and snapshots[uid]['tradeable']:
                    self._close(db, state, uid, snapshots[uid], 'Лимит риска', clock)
                    closed.add(uid)
        return closed

    def _open(self, db, state, uid, s, features, side, clock):
        from .trading_rules_v2 import VERSION,entry_block
        block=entry_block(state,uid,s,clock,features,side)
        if block:return block
        if state.get('strategy_version')==VERSION and s.get('trend_1h',{}).get('direction')!=side:
            return 'Направление входа не подтверждено часовым трендом; сделка пропущена.'
        risk = state['risk']
        c = s['contract']
        equity = D(account_values(state)['equity'])
        if len(state['positions']) >= risk['max_positions']:
            return 'Достигнут лимит открытых позиций.'
        if uid in state['positions']:
            return 'Позиция уже открыта; наращивание в первой версии выключено.'
        tick, tick_value = D(c['tick']), D(c['tick_value'])
        stop_ticks = max(2, int((D(features['atr'])*2/tick).to_integral_value(rounding=ROUND_CEILING)))
        fee = D(risk['commission'])
        loss_one = stop_ticks*tick_value+2*fee+2*risk['slippage_ticks']*tick_value
        held_risk=sum((max(D(0),(D(p['last_price'])-D(p['stop']))/D(p['tick'])*D(p['tick_value'])*(1 if p['side']=='long' else -1))*p['contracts']
            +(2*D(risk['commission'])+2*risk['slippage_ticks']*D(p['tick_value']))*p['contracts'] for p in state['positions'].values()),D(0))
        risk_budget = max(D(0),min(equity*D(risk['risk_pct'])/100,equity*D(risk.get('portfolio_risk_pct','5'))/100-held_risk))
        margin_one = D(c['margin_'+side])*(1+D(risk['margin_buffer_pct'])/100)
        used = D(account_values(state)['margin'])
        capacity = min(equity*D(risk['max_margin_pct'])/100-used, equity-used)
        size = min(risk['max_contracts'], int(risk_budget/loss_one), max(0, int(capacity/(margin_one+fee))))
        if size < 1:
            return f'Даже 1 контракт не проходит лимиты: ожидаемый риск {cash(loss_one)} ₽, бюджет риска {cash(risk_budget)} ₽, ГО с запасом {cash(margin_one)} ₽.'
        direction = 1 if side == 'long' else -1
        fill = self._fill(s, direction, risk)
        if fill <= 0:
            return 'Некорректная цена исполнения после проскальзывания.'
        if fill-direction*stop_ticks*tick <= 0 or fill+direction*stop_ticks*2*tick <= 0:
            return 'Защитные уровни выходят за допустимую положительную цену.'
        if state.get('strategy_version')==VERSION:state.setdefault('reentry_required',{}).pop(uid,None)
        paid = fee*size
        state['cash'] = str(D(state['cash'])-paid)
        state['realized_pnl'] = str(D(state['realized_pnl'])-paid)
        state['positions'][uid] = {'uid': uid, 'ticker': c['ticker'], 'side': side, 'contracts': size,
            'entry_margin':str(margin_one*size),'entry_fee':cash(paid),
            'entry': str(fill), 'last_price': s['price'], 'accrued_pnl': str((D(s['price'])-fill)/tick*tick_value*direction*size), 'reserved_margin': str(margin_one*size),
            'stop': str(fill-direction*stop_ticks*tick), 'target': str(fill+direction*stop_ticks*2*tick),
            'opened_at': clock.isoformat(), 'quoted_at': s['quoted_at'], 'tick': c['tick'], 'tick_value': c['tick_value']}
        self._trade(db, state, uid, 'open_'+side, {'ticker': c['ticker'], 'side': side, 'contracts': size,
                    'price': str(fill), 'fee': cash(paid), 'stop_ticks': stop_ticks,
                    'risk_rub': cash(loss_one*size), 'entry_margin':str(margin_one*size), 'quoted_at': s['quoted_at']}, clock)
        return 'Открыта виртуальная позиция: '+side

    def _acquire(self, clock):
        owner = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM lab_lock WHERE id=1').fetchone()
            if row and row['expires_at'] > clock.isoformat():
                return None
            db.execute('INSERT OR REPLACE INTO lab_lock VALUES(1,?,?)', (owner, (clock+timedelta(minutes=30)).isoformat()))
        return owner

    def cycle(self, market, clock=None, ai=None, decisions=True):
        supplied_clock = clock
        clock = clock or utcnow()
        if clock.tzinfo is None:
            raise ValueError('Нужен часовой пояс цикла.')
        owner = self._acquire(clock)
        if not owner:
            return {'status': 'busy', 'note': 'Предыдущий цикл ещё выполняется.'}
        errors = []
        try:
            with self.db() as db:
                state = self._load(db)
            snapshots = {}
            for uid in (state['watchlist'] if decisions else state['positions']):
                try:
                    snapshots[uid] = market.snapshot(uid, supplied_clock)
                except Exception as exc:
                    errors.append(state['watchlist'][uid]['ticker']+': '+str(exc)[:200])
            if supplied_clock is None:
                clock = utcnow()
            with self.db() as db, localcontext() as ctx:
                ctx.prec = 40
                db.execute('BEGIN IMMEDIATE')
                state = self._load(db)
                closed = self._mark(db, state, snapshots, clock)
                for uid, s in snapshots.items():
                    if uid in state['watchlist']:
                        state['watchlist'][uid] = s['contract']
                    state['marks'][uid] = {'price': s['price'], 'quoted_at': s['quoted_at'], 'tradeable': s['tradeable']}
                self._save(db, state)
            for uid, s in snapshots.items():
                if not decisions:continue
                if uid in closed:
                    continue
                try:
                    features = indicators(s['candles'])
                    features['uid'] = uid
                    if 'trend_1h' in s:features['trend_1h']=s['trend_1h']
                except ValueError as exc:
                    errors.append(s['contract']['ticker']+': '+str(exc))
                    continue
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    state = self._load(db)
                    if not state['enabled'] or state['strategy'] == 'connector' or uid not in state['watchlist']:
                        continue
                    if db.execute('SELECT 1 FROM lab_decisions WHERE uid=? AND candle_at=?', (uid, features['candle_at'])).fetchone():
                        continue
                    # Reserve the decision before an external model call: never bill it twice for the same candle.
                    detail = {'ticker': s['contract']['ticker'], 'action': 'hold', 'reason': 'Цикл зарезервирован.', 'status': 'reserved'}
                    cursor = db.execute('INSERT INTO lab_decisions(at,uid,candle_at,payload) VALUES(?,?,?,?)',
                        (clock.isoformat(), uid, features['candle_at'], json.dumps(detail)))
                    decision_id = cursor.lastrowid
                    revision = state['config_revision']
                    strategy = state['strategy']
                    position = state['positions'].get(uid)
                block = None
                expiry = s['contract'].get('expires_at')
                candle_dt = datetime.fromisoformat(features['candle_at'].replace('Z', '+00:00'))
                if not s['tradeable']:
                    block = 'Рынок закрыт или торговля через API недоступна.'
                elif clock-candle_dt > timedelta(minutes=90):
                    block = 'Последняя завершённая свеча устарела.'
                elif clock-datetime.fromisoformat(s['quoted_at'].replace('Z','+00:00'))>timedelta(seconds=state.get('entry_quote_seconds',900)):
                    block = 'Последняя котировка недостаточно свежая для нового входа.'
                elif expiry and datetime.fromisoformat(expiry.replace('Z','+00:00')) <= clock+timedelta(days=1):
                    block = 'До экспирации меньше суток; автоматического переноса контракта нет.'
                elif any(p not in snapshots for p in state['positions']):
                    block = 'Часть открытых позиций не имеет свежей оценки.'
                proposal = {'action': 'hold', 'confidence': 0, 'reason': block or ''}
                if not block:
                    if strategy == 'rules':
                        proposal = baseline(features, position)
                    else:
                        with self.db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            day = clock.astimezone(TZ).date().isoformat()
                            count = db.execute('SELECT COUNT(*) FROM lab_ai_calls WHERE day=?', (day,)).fetchone()[0]
                            batch=bool(ai is not None and getattr(ai,'batch_verified',False))
                            if count >= 24 and not batch:
                                block = 'Лимит 24 запросов ИИ за день достигнут.'
                            else:
                                if not batch:db.execute('INSERT INTO lab_ai_calls(day,at,provider) VALUES(?,?,?)',
                                           (day, clock.isoformat(), ai_configuration()['provider']))
                        if not block:
                            try:
                                proposal = Proposal.model_validate((ai or propose)(features, position)).model_dump()
                            except Exception:
                                block = 'Не удалось получить проверенное решение ИИ. Новая сделка пропущена.'
                with self.db() as db, localcontext() as ctx:
                    ctx.prec = 40
                    db.execute('BEGIN IMMEDIATE')
                    state = self._load(db)
                    result = block
                    if not result and (not state['enabled'] or revision != state['config_revision']):
                        result = 'Настройки изменены или включена пауза во время анализа.'
                    quoted = datetime.fromisoformat(s['quoted_at'].replace('Z','+00:00'))
                    execution_clock = supplied_clock or utcnow()
                    if not result and execution_clock-quoted > timedelta(seconds=state.get('entry_quote_seconds',900)):
                        result = 'Котировка устарела во время анализа; вход отменён.'
                    if not result:
                        action = proposal['action']
                        if action == 'close' and uid in state['positions']:
                            self._close(db, state, uid, s, proposal['reason'], clock)
                            result = 'Виртуальная позиция закрыта.'
                        elif action in ('long', 'short'):
                            if strategy == 'ai' and proposal['confidence'] < .65:
                                result = 'Самооценка сигнала ниже 0.65; вход пропущен.'
                            else:
                                result = self._open(db, state, uid, s, features, action, clock)
                        else:
                            result = 'Без новой сделки.'
                    detail = {'ticker': s['contract']['ticker'], 'strategy': strategy, 'strategy_version':state.get('strategy_version','legacy'), **proposal,
                              'result': result, 'features': features, 'status': 'complete', 'model': state.get('cloud',{}).get('model') or (ai_configuration()['model'] if strategy == 'ai' else 'rules-v1')}
                    db.execute('UPDATE lab_decisions SET payload=? WHERE id=?', (json.dumps(detail, ensure_ascii=False), decision_id))
                    self._save(db, state)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                state = self._load(db)
                state['last_cycle'] = clock.isoformat()
                values = account_values(state)
                note = '; '.join(errors)[:1500] or state.get('auto_pause_reason') or ('Цикл завершён.' if state['enabled'] else 'Новые позиции на паузе; оценки и защитные выходы проверены.')
                db.execute('INSERT INTO lab_runs(at,status,note) VALUES(?,?,?)', (clock.isoformat(), 'partial' if errors else 'ok', note))
                db.execute('INSERT INTO lab_equity(at,equity,payload) VALUES(?,?,?)', (clock.isoformat(), values['equity'], json.dumps(values)))
                self._save(db, state)
            return {'status': 'partial' if errors else 'ok', 'note': note}
        finally:
            with self.db() as db:
                db.execute('DELETE FROM lab_lock WHERE owner=?', (owner,))

    def heartbeat(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            state['worker_heartbeat'] = utcnow().isoformat()
            self._save(db, state)

    def close_position(self, uid, market, clock=None):
        clock = clock or utcnow()
        snapshot = market.snapshot(uid, clock)
        if not snapshot['tradeable']:
            raise ValueError('Нет доступного рынка для достоверного виртуального исполнения.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self._load(db)
            if uid not in state['positions']:
                raise LabConflict('Виртуальная позиция уже закрыта.')
            self._close(db, state, uid, snapshot, 'Ручное закрытие виртуальной позиции', clock)
            state['config_revision'] += 1
            values = account_values(state)
            db.execute('INSERT INTO lab_equity(at,equity,payload) VALUES(?,?,?)', (clock.isoformat(), values['equity'], json.dumps(values)))
            self._save(db, state)


def baseline(features, position):
    close, fast, slow = (D(features[k]) for k in ('last_close', 'sma_fast', 'sma_slow'))
    if position and ((position['side'] == 'long' and fast < slow) or (position['side'] == 'short' and fast > slow)):
        return {'action': 'close', 'reason': 'Правила: быстрый тренд сменил направление.'}
    action = 'long' if close > D(features['breakout_high']) and fast > slow else 'short' if close < D(features['breakout_low']) and fast < slow else 'hold'
    return {'action': action, 'reason': 'Правила, не ИИ: пробой 20 свечей и согласование SMA10/SMA30.' if action != 'hold' else 'Правила: подтверждённого пробоя нет.'}
