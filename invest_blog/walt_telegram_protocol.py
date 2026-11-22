"""Authenticated private button actions; no broker operations in this module."""
import base64,hashlib,hmac,json,re,secrets,time
from html import escape
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD=b'content-hub-walt-v1'


def b64(data):return base64.urlsafe_b64encode(data).decode().rstrip('=')
def unb64(value):return base64.urlsafe_b64decode(value+'='*((-len(value))%4))


def button(body,owner,key):
    sig=b64(hmac.new(key.encode(),(body+':'+str(owner)).encode(),hashlib.sha256).digest()[:9])
    value='wb:'+body+':'+sig
    if len(value.encode())>64:raise ValueError('Слишком длинная кнопка.')
    return value


def verify_button(value,owner,key,clock=time.time):
    if not isinstance(value,str) or len(value.encode())>64 or not value.startswith('wb:'):raise ValueError('Неизвестная кнопка.')
    body,sig=value[3:].rsplit(':',1)
    if not hmac.compare_digest(button(body,owner,key),value):raise ValueError('Подпись кнопки не совпала.')
    parts=body.split(':');action=parts[0]
    if action=='q' and len(parts)==5:
        if not parts[1].isdigit() or not parts[2].isdigit() or parts[3] not in ('l','s'):raise ValueError('Неверный расчёт.')
    elif action in ('e','c','r') and len(parts)==3:
        if not re.fullmatch(r'[a-f0-9]{16}',parts[1]):raise ValueError('Неверное подтверждение.')
    elif action=='n' and len(parts)==3:
        if not parts[1].isdigit():raise ValueError('Неверный обзор.')
    else:raise ValueError('Неизвестное действие.')
    if not parts[-1].isdigit() or not clock()<int(parts[-1])<=clock()+24*3600+30:raise ValueError('Кнопка устарела. Запроси новую проверку.')
    return parts


def seal_event(event,key):
    nonce=secrets.token_bytes(12)
    return 'v1.'+b64(nonce+AESGCM(unb64(key)).encrypt(nonce,json.dumps(event,separators=(',',':')).encode(),AAD))


def open_event(envelope,key,owner,button_key,clock=time.time):
    try:
        if not isinstance(envelope,str) or not envelope.startswith('v1.') or len(envelope)>5000:raise ValueError
        raw=unb64(envelope[3:]);event=json.loads(AESGCM(unb64(key)).decrypt(raw[:12],raw[12:],AAD))
        if event.get('owner')!=str(owner) or event.get('chat')!=str(owner):raise ValueError
        if not re.fullmatch(r'[0-9]{1,20}',str(event.get('message',''))):raise ValueError
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',event.get('query','')):raise ValueError
        if not 0<=clock()-event['created']<=120:raise ValueError
        verify_button(event['data'],owner,button_key,clock)
        return event
    except Exception:raise ValueError('Подтверждение Telegram не прошло проверку; исполнения не было.') from None


def signal_keyboard(packet,run,owner,key,clock=time.time):
    from .cloud_signals import display_rows
    expiry=int(clock()+2*3600);rows=[]
    for row in display_rows(packet['rows']):
        symbol=row['symbol'];ident=row['market_id']
        if type(ident) is not int or ident<0 or not str(run).isdigit():raise ValueError('Неверный инструмент кнопки.')
        suggested=row['side']
        if suggested not in ('long','short'):
            trend=row.get('scenario',{}).get('trend_4h');suggested='short' if trend=='down' else 'long'
        sides=('short','long') if suggested=='short' else ('long','short')
        rows.append([{'text':('↘ Шорт ' if side=='short' else '↗ Лонг ')+symbol,
            'callback_data':button(f'q:{run}:{ident}:{"s" if side=="short" else "l"}:{expiry}',owner,key)} for side in sides])
    rows.append([{'text':'⚡ Следующий сигнал','callback_data':button(f'n:{run}:{expiry}',owner,key)}])
    return {'inline_keyboard':rows}


def draft_card(draft):
    p=draft['proposal'];actual=draft.get('actual_position');status=draft['status']
    side='лонг' if p['side']=='long' else 'шорт'
    title='Позиция открыта · защита сверена' if status=='protected' else 'Проверь сделку' if status=='previewed' else 'Расчёт заблокирован · сделка не открыта' if status=='blocked' else 'Проверь состояние Walt'
    lines=[f'<b>{title}</b>\n{escape(p["symbol"])} · {side}',
        f'Маржа: <b>{escape(p["margin"])} {escape(p["currency"])}</b> · плечо {p["leverage"]}×',
        f'Риск с расходами: {escape(p["risk_estimate"])} {escape(p["currency"])}',
        f'Стоп: <b>{escape(p["stop"])}</b> · тейк: <b>{escape(p["take"])}</b>']
    if p.get('profit_estimate'):lines.append('Оценка прибыли у тейка: '+escape(p['profit_estimate'])+' '+escape(p['currency']))
    if p.get('funding_hour_estimate'):lines.append('Оценка фандинга в час: '+escape(p['funding_hour_estimate'])+' '+escape(p['currency'])+' (плюс — расход, минус — поступление; ставка меняется).')
    if actual:lines.append('Фактический вход: '+escape(str(actual.get('avg_entry_price')))+' · ликвидация Walt: '+escape(str(actual.get('liquidation_price'))))
    elif p.get('liquidation'):
        label='Граница защиты — расчёт хаба, не цена Walt' if p.get('liquidation_source')=='local_conservative_estimate' else 'Оценка ликвидации Walt'
        lines.append(label+': '+escape(p['liquidation']))
    for key in ('telegram_notice','block_reason','alert'):
        if draft.get(key):lines.append(escape(draft[key]))
    if draft.get('ai',{}).get('side')!=p['side']:lines.append('Направление выбрано вручную; это не подтверждённый ИИ вход.')
    if status=='previewed':lines.append('Это реальные деньги. Открытие — только по отдельной кнопке подтверждения. Если срок истёк, потребуется новая проверка.')
    if status=='blocked':lines.append('Подтверждение недоступно: проверка не пройдена. Этот расчёт не отправлялся на исполнение. Нужен новый расчёт с допустимыми параметрами или новый сигнал.')
    return '\n\n'.join(lines)
