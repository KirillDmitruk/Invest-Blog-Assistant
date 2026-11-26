"""Compact Telegram summaries built exclusively from persisted virtual ledgers."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation

MSK = timezone(timedelta(hours=3))

def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def amount(value, signed=False):
    value = number(value)
    if value is None:
        return 'нет данных'
    return format(value, '+,.2f' if signed else ',.2f').replace(',', '\u202f').replace('.', ',')

def price(value):
    value = number(value)
    if value is None:
        return '—'
    return format(value, '.6f').rstrip('0').rstrip('.').replace('.', ',')

def result(value, basis, unit):
    value, basis = number(value), number(basis)
    if value is None:
        return 'результат не подтверждён'
    pct = amount(value / basis * 100, True) + '%' if basis is not None and basis > 0 else '% нет данных'
    return f'{amount(value, True)} {unit} ({pct})'

def stamp(value):
    try:
        dt = datetime.fromtimestamp(float(value), timezone.utc) if number(value) is not None else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(MSK).strftime('%d.%m · %H:%M МСК')
    except (ValueError, TypeError, OSError, OverflowError):
        return 'время снимка не подтверждено'

def side(value):
    return '↗ Лонг' if value == 'long' else '↘ Шорт'

def render(name, unit, equity, initial, at, positions, events, warnings=()):
    change = number(equity) - number(initial)
    lines = [f'🧪 {name} · ДЕМО', stamp(at), '', f'💼 Стоимость: {amount(equity)} {unit}',
             f'📊 От старта: {result(change, initial, unit)}']
    for event in events:
        lines += ['', event]
    lines += ['', f'📂 Открытые позиции · {len(positions)}']
    lines += positions or ['Позиций нет · ждём подходящий вход.']
    if warnings:
        lines += ['', '⚠️ ' + ' · '.join(str(w)[:200] for w in warnings[:2])]
    lines += ['', 'Только виртуальные деньги. Общий итог включает открытый PnL.',
              '% позиций — к марже/ГО; открытый PnL до комиссии выхода.']
    return '\n'.join(lines)

def walt_summary(state, changes=()):
    unit='$';positions=[];events=[]
    for p in state['positions'].values():
        gross=(number(p['mark'])-number(p['entry']))*number(p['quantity'])*(1 if p['side']=='long' else -1)
        net=gross-number(p['entry_fee'])-number(p['funding_paid'])
        positions.append(f'{side(p["side"])} {p["symbol"]} · {p["leverage"]}×\n'
                        f'   {result(net,p["margin"],unit)} · маржа {amount(p["margin"])} {unit}')
    reasons={'stop':'стоп','stop_quote':'стоп','take':'тейк','take_quote':'тейк','liquidation_model':'ликвидация в модели'}
    for e in changes:
        if e['kind']=='open':
            p=next((p for p in state['positions'].values() if p['symbol']==e['symbol'] and p['side']==e['side']),None)
            if p:
                events.append(f'🟦 Открыта · {p["symbol"]} · {side(p["side"])} {p["leverage"]}×\n'
                              f'Маржа {amount(p["margin"])} {unit} · вход {price(p["entry"])}\n'
                              f'Стоп {price(p["stop"])} · тейк {price(p["take"])}')
        elif e['kind']=='close':
            p=next((p for p in reversed(state['closed']) if p['symbol']==e['symbol'] and p['side']==e['side'] and p['closed_at']==e['at']),None)
            value=p['net_pnl'] if p else e['pnl']
            events.append(f'{"✅" if number(value)>=0 else "🔴"} Закрыта · {e["symbol"]} · {side(e["side"])}\n'
                          f'{result(value,p["margin"] if p else None,unit)} · {reasons.get(e["reason"],e["reason"])}\n'
                          'Итог после комиссий и фандинга.')
    warnings=list(state.get('warnings',[]))
    if state.get('paused'):warnings.insert(0,'Новые входы приостановлены: '+state.get('reason',''))
    return render('Walt',unit,state['equity'],state['initial_balance'],state.get('last_cycle'),positions,events,warnings)

def futures_summary(before, after, changes=()):
    s=after['state'];unit='₽';positions=[];events=[]
    def opening(uid):
        return next((t['detail'] for t in sorted(before['trades'],key=lambda t:t['id'],reverse=True)
                     if t['uid']==uid and t['action'].startswith('open_')), {})
    for uid,p in s['positions'].items():
        fee=p.get('entry_fee')
        if fee is None:fee=opening(uid).get('fee')
        net=number(p['accrued_pnl'])-number(fee) if number(fee) is not None else None
        positions.append(f'{side(p["side"])} {p["ticker"]} · {p["contracts"]} контр.\n'
                        f'   {result(net,p["reserved_margin"],unit)} · ГО {amount(p["reserved_margin"])} {unit}')
    for t in sorted(changes,key=lambda t:t['id']):
        d=t['detail'];direction=d.get('side','long' if t['action']=='open_long' else 'short')
        if t['action'].startswith('open_'):
            p=s['positions'].get(t['uid'],{})
            events.append(f'🟦 Открыта · {t["ticker"]} · {side(direction)} · {d["contracts"]} контр.\n'
                          f'ГО {amount(d.get("entry_margin"))} {unit} · вход {price(d["price"])}\n'
                          f'Стоп {price(p.get("stop"))} · тейк {price(p.get("target"))}')
        elif t['action']=='close':
            old=before['state']['positions'].get(t['uid'],{});op=opening(t['uid'])
            fee=d.get('entry_fee')
            if fee is None:fee=old.get('entry_fee',op.get('fee'))
            basis=d.get('entry_margin') or old.get('entry_margin') or op.get('entry_margin')
            net=number(d['pnl_before_exit_fee'])-number(d['fee'])-number(fee) if number(fee) is not None else None
            events.append(f'{"✅" if net is not None and net>=0 else "🔴"} Закрыта · {t["ticker"]} · {side(direction)}\n'
                          f'{result(net,basis,unit)} · {str(d["reason"])[:160]}\nИтог после комиссий.')
    warnings=[]
    if s.get('auto_pause_reason'):warnings.append(s['auto_pause_reason'])
    return render('Т‑Инвестиции',unit,after['account']['equity'],s['budget'],s.get('last_cycle'),positions,events,warnings)
